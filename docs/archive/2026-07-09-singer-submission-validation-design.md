# Auto-resolving singer submissions (download validation + fallback)

- **Date**: 2026-07-09
- **Status**: Design approved, pending spec review → implementation plan
- **Author**: KJ (Andrew) + Claude
- **Motivating incident**: 2026-07-09 live show — a singer requested "Beetlejuice (musical) –
  Say My Name". The chosen result pointed at YouTube video `_vMTtVPhd80`, which is a **private
  video**, so the download failed (`ERROR: [youtube] _vMTtVPhd80: Private video`). Nothing tried
  an alternative; the KJ discovered the failure only after the singer was already in rotation and
  fixed it by hand (`download-and-link` to a KaraFun "Karaoke Version" upload).

## Problem

Singers don't paste URLs — they search (`/sing/search`) and pick a version. Search uses yt-dlp's
`extract_flat` mode, which is fast but **never verifies each video is actually playable**, so a
private/deleted video looks like a normal result. The chosen URL is stored on submit with **no
accessibility check**, and the download is only attempted later (at auto- or KJ-approval). That
attempt is the first time anyone learns the video is dead — and there is **no auto-fallback**, so
recovery is fully manual.

Result: the singer is told "submitted!" for a song that cannot play, creating mismanaged
expectations, and the KJ must intervene live.

## Goals

1. A singer submission that can't be downloaded should **self-heal** by automatically trying the
   next-best candidate version of the same song.
2. The singer should only be told there's a problem when **no** candidate is playable.
3. **No false rejections**: a flaky network / provider blip must not be reported as "unavailable"
   or burn through good candidates.
4. Zero added latency on the common (first candidate works) case.

## Non-goals

- Detecting "accessible but not actually a karaoke version / wrong song / has lead vocals". A
  download cannot determine this; that remains the KJ's review responsibility.
- Changing the search ranking or the `kj_pick` deferral behaviour for multi-version songs.
- Validating `local` / `divebar` picks (already point at real files on disk).

## Key decisions

1. **The download attempt is the probe.** yt-dlp performs a full metadata extract before
   downloading — that is precisely what surfaced `Private video`. So rather than a separate probe
   pass (which would add a round-trip before every *successful* download too), the worker attempts
   the best candidate and, on an unavailability-class failure, falls back to the next candidate.
   Same honesty, no happy-path latency, reuses the existing download path.
2. **Auto-fallback across a ranked candidate list**, capped at 3 tried candidates.
3. **Asynchronous + notify.** Submit returns instantly ("Submitted! Finding your video…").
   Resolution runs in the existing background download worker. The singer learns of a version
   change or a terminal failure through the mechanisms already built for this: `/my-requests`
   polling and Web Push.
4. **Fallback triggers only on definitively-unavailable errors.** Transient errors retry the same
   candidate — they do not consume the candidate list.

## Architecture

One new concept, a **candidate list**, threaded through the existing pipeline.

### Components

- **Client — `static-sing/sing.js`**
  Already attaches the ranked `versions[]` snapshot in `source_meta` for multi-version (`kj_pick`)
  picks. Extend it to *always* attach the group's ranked `versions[]`, including for single direct
  `youtube`/`kn` picks. No extra cost — the client already holds these from the search response.

- **Resolver — new module `sing_resolve.py`** (pure, unit-testable; no I/O)
  - `next_candidate(candidates, tried)` → next untried candidate (any source), or `None`.
  - `classify_error(exc_or_message)` → `UNAVAILABLE` | `TRANSIENT`.
    - `UNAVAILABLE`: `Private video`, `Video unavailable`, `This video has been removed`,
      `blocked in your country`, account-terminated, age/geo hard blocks.
    - `TRANSIENT`: socket/read timeouts, HTTP 429/5xx, `bgutil`/PO-token provider errors,
      connection resets, generic network failures.
  - Default-safe: an **unrecognised** error is treated as `TRANSIENT` (retry same candidate)
    rather than `UNAVAILABLE`, to avoid false fallthrough.

- **Download worker — `routes.py:_download_worker`**
  On a download failure for a sing-request-backed item, consult the resolver:
  - `UNAVAILABLE` → mark this candidate tried; pick `next_candidate`; if one exists, swap the
    request's bound source (`sing_store.update_request_source`) and re-enqueue/retry; if the cap is
    hit or none remain, go to the terminal "needs KJ" state.
  - `TRANSIENT` → retry the **same** candidate with bounded backoff; do not consume the list.
  - Dedup: before re-downloading a fallback candidate, reuse `_existing_media_for` — if a copy is
    already on disk (or the same song has a local/Divebar version in the list), link it instantly.

- **Notification**
  - Request row status/source already drives the singer's `/my-requests` view — updating it is the
    baseline signal (no new polling).
  - Fire **one** Web Push via `push_dispatcher` only on a terminal state:
    - version changed → "Locked in — using the <label> version".
    - all candidates failed → "We couldn't find a playable version — your KJ has been notified".
  - No push per intermediate attempt.

- **KJ visibility**
  The rotation entry reflects the outcome: auto-recovered entries show the substituted version;
  all-fail entries surface in the existing failed-download UI (the red ❌ / ⚠️) flagged as
  "needs attention" so the KJ can link manually — exactly the manual path used tonight.

### Data flow

```
submit → request (source_meta.versions = ranked candidates, best-first)
   ↓ approve (auto OR KJ)  →  enqueue download item + candidate list
   ↓ _download_worker:
       try candidate[0] ── ok ──────────────→ link file, status=complete
          │ UNAVAILABLE
          ↓ candidate[1] ── ok ─────────────→ link file, update_request_source,
          │                                    push "locked in (<label>)"
          ↓ … cap at 3 tried
          └ all fail ──→ status="unavailable", push "no playable version — KJ notified",
                          rotation entry flagged ⚠️ for KJ
       (TRANSIENT error → retry same candidate w/ backoff; list not consumed)
```

Fallback candidates may be **any source**. A dead YouTube pick can fall back to a local or Divebar
version of the same song present in the list — linked instantly, the best possible recovery.

Because both auto-approve (submit) and KJ-approve enqueue into the same `app.download_queue`, this
logic covers both approval paths with no per-path duplication.

## Error handling

The primary risk is a transient blip (network, or the `bgutil` PO-token helper being down — which
it was during the motivating incident) cascading through every candidate and wrongly reporting
"nothing works". Mitigations:

- Fallback only on `UNAVAILABLE`; `TRANSIENT` retries the same candidate.
- Unrecognised errors default to `TRANSIENT`.
- Bounded ret/backoff and a hard cap (3 candidates) to keep resolution time predictable during a
  live show.

## Testing

- **Unit (`sing_resolve.py`)**
  - `classify_error` against real yt-dlp strings, including the incident's
    `ERROR: [youtube] _vMTtVPhd80: Private video`; timeouts / 429 / `bgutil` → `TRANSIENT`;
    unknown string → `TRANSIENT`.
  - `next_candidate` ordering, `tried` exclusion, cap, exhaustion, mixed-source lists.
- **Integration (worker with a stub downloader)**
  - c0 `UNAVAILABLE` → c1 succeeds → source updated, entry linked, one "locked in" push.
  - all candidates `UNAVAILABLE` → request `status=unavailable`, KJ-flagged, one "no version" push.
  - `TRANSIENT` error → same candidate retried, candidate list not consumed, no push.
  - fallback candidate already on disk → linked via `_existing_media_for`, no re-download.

## Deployment note

This is a **backend** change: it requires `systemctl restart kj-controller`, which interrupts
active playback. It must be deployed in a maintenance window, never mid-show.

## Open questions

None blocking. Candidate cap (3) and backoff timing are tunable during implementation.
