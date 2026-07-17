# Plan: Auto-resolving singer submissions (download validation + fallback)

**Created:** 2026-07-09
**Branch:** feat/sess-20260717-0153-singer-submission-validation
**Status:** Implemented (pending review/merge)
**Design spec:** [2026-07-09-singer-submission-validation-design.md](./2026-07-09-singer-submission-validation-design.md)

## As-Built Deviations

Discovered during implementation (all reflected in the code + tests):

- **D1 — failure reason via `media._last_error`, not a caught exception.** `download_video`
  swallows yt-dlp errors and returns `(None, None)`; it now records the reason on
  `media._last_error`, which the single (serialized) download worker reads. No second network probe.
- **D2 — transient exhaustion advances, not terminates.** Transient retries per candidate then fall
  through to the next candidate; only an exhausted candidate list is terminal. Unknown errors
  default to transient.
- **D3 — no new sing-request status.** Terminal = the existing rotation `failed` download_status +
  an `unavailable` push; success rebinds via the existing `update_request_source`.
- **D4 — pivot from the planned client change.** Direct `youtube`/`kn` picks are only ever
  single-version (no alternates), so attaching `versions[]` client-side adds nothing. The real fix
  is `_preserve_versions_meta`: keep the `versions[]` snapshot through `kj_pick` binding (both the
  admin approve route and `resolve_kj_pick_best`) so multi-version songs — the incident case — have
  candidates. `sing.js` was **not** changed.
- **D5 — v1 scope: YouTube-type fallback only.** Cross-source (local/Divebar) fallback is a
  documented follow-up.
- Open questions resolved: push uses the existing `notify_request_decision` (+ two new copy steps);
  no `unavailable` status enum needed; caps kept at 3 candidates / 2 transient retries.

## Overview

When a singer's picked YouTube version can't be downloaded (private/deleted video, as in the
2026-07-09 live incident), the download worker should automatically fall back to the next-best
candidate version of the same song, notifying the singer only when nothing is playable. Async, no
false rejections on transient network blips, zero added latency on the happy path.

## Requirements

- [ ] On a download failure classified as **unavailable**, auto-advance to the next candidate
      version and retry, without KJ intervention.
- [ ] On a failure classified as **transient** (timeout / 429 / `bgutil` / network), retry the
      **same** candidate — do not consume the candidate list.
- [ ] Cap total resolution effort: ≤ 3 distinct candidates tried; bounded transient retries.
- [ ] When a fallback succeeds, rebind the request's source so `/my-requests` reflects the version
      actually used; when all candidates fail, mark the request terminally "unavailable" and flag
      the rotation entry for the KJ.
- [ ] Singer is notified on terminal states only (version-changed / no-version), via existing
      `/my-requests` polling (baseline) and Web Push (enhancement).
- [ ] Single direct `youtube`/`kn` picks carry the group's ranked `versions[]` so they have
      fallback candidates too.
- [ ] Non-functional: no submit-path latency added; worker stays single-threaded and never
      sleep-blocks the queue; no infinite re-queue loops.

## Technical Approach

**Reuse over new machinery.** The "candidate list" is the existing `versions[]` snapshot already
stored in `source_meta` (today only for `kj_pick`). Fallback = advance an index over that list and
reuse `_pick_version_from_kj_pick`'s translation of `versions[i] → (source_type, source_ref,
source_meta)`.

**Fallback lives in the download worker's existing error branch** (`routes.py:713–739`). Today that
branch discards the exception and marks the item `error`. We change it to:
1. Capture the exception message.
2. For **sing-request-backed** items only (identified by a new `request_id` + `candidates` on the
   queue item), consult the resolver.
3. `unavailable` + candidates remain under cap → translate next candidate, `update_request_source`,
   reset the item to `queued` (worker re-picks it), continue.
4. `transient` under retry cap → bump an attempt counter, reset to `queued` (back of line — no
   `sleep`, since the worker is sequential), continue.
5. Exhausted / terminal → mark request `unavailable`, flag rotation entry, fire one Web Push.

Non-sing downloads (KJ manual, divebar) keep today's behaviour untouched.

**Error classification** is isolated in a pure, unit-tested module so the risky part (deciding
unavailable vs transient) is testable without yt-dlp or the network. Unknown errors default to
`transient` (safer: retry same candidate rather than wrongly discarding a good one).

### Trade-offs considered

- *Separate metadata probe vs download-as-probe* → download-as-probe (per spec): no happy-path
  latency, reuses the download path.
- *Transient retry inline (`sleep`) vs re-queue* → re-queue with attempt counter, because the
  single worker thread must not block other singers' downloads.
- *New candidate schema vs reuse `versions[]`* → reuse; `_pick_version_from_kj_pick` already
  translates it and the client already produces it.

## Implementation Steps

1. [ ] **`sing_resolve.py` (new, pure).**
   - `classify_error(message: str) -> "unavailable" | "transient"` with pattern tables
     (unavailable: `Private video`, `Video unavailable`, `has been removed`, `blocked in your
     country`, account-terminated; transient: timeouts, `HTTP Error 429/5xx`, `bgutil`, connection
     reset). Unknown → `transient`.
   - `next_candidate_index(total, tried) -> int | None` and a `MAX_CANDIDATES = 3`,
     `MAX_TRANSIENT_RETRIES = 2` constants.
2. [ ] **Unit tests `tests/test_sing_resolve.py`** — real yt-dlp strings incl. the incident's
   `ERROR: [youtube] _vMTtVPhd80: Private video`; ordering/cap/exhaustion; unknown→transient.
3. [ ] **Client `static-sing/sing.js`** — attach the group's ranked `versions[]` in `source_meta`
   for direct `youtube`/`kn` submissions (mirror the existing `kj_pick` line ~658). Preserves
   existing single-pick behaviour otherwise.
4. [ ] **`approve_sing_request` (routes.py ~4844 queue_item build)** — for `youtube`/`kn`, add to
   the queue item: `request_id`, `candidates` (the `versions[]` list, may be empty), `current_index`
   (0), `tried` ([]), `transient_attempts` (0). No behaviour change when `candidates` is empty
   beyond richer terminal messaging.
5. [ ] **`_download_worker` error branch (routes.py:713–739)** — capture `exc`; extract
   `_attempt_sing_fallback(app, item, str(exc))` helper that implements steps 3–5 of the approach.
   Keep non-sing items on the current path.
6. [ ] **`sing_store.py`** — add a terminal status transition (e.g. `mark_unavailable(request_id,
   reason)`) and confirm `update_request_source` is used for successful rebinds. Ensure the status
   enum/index covers `unavailable`.
7. [ ] **Singer-facing surface** — extend `_public_request_view` (sing.py) to expose the resolution
   state + the version label actually used, so `/my-requests` renders "finding…/locked in
   (label)/unavailable" without a new endpoint.
8. [ ] **Web Push (enhancement) `push_dispatcher.py`** — add a targeted per-request send for the two
   terminal states. *Open question:* confirm the dispatcher exposes a per-singer/subscription send
   (vs only rotation-mutation broadcasts). If not trivially available, ship steps 1–7 first
   (polling covers the UX) and add push as a follow-up.
9. [ ] **Integration tests** — worker + stub downloader: (a) c0 unavailable→c1 ok (rebind + one
   push), (b) all unavailable (terminal + KJ flag + one push), (c) transient→same candidate retried,
   list not consumed, (d) fallback candidate already on disk → linked via `_existing_media_for`,
   no re-download.
10. [ ] **Docs** — `docs/ARCHITECTURE.md` (new module + fallback flow), `docs/CHANGELOG.md` (dated
    entry), `docs/TROUBLESHOOTING.md` (what "unavailable — KJ notified" means).

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/sing_resolve.py` | Create | Pure error classifier + candidate iteration + caps |
| `kj-controller/tests/test_sing_resolve.py` | Create | Unit tests for the resolver |
| `kj-controller/static-sing/sing.js` | Modify | Attach `versions[]` for direct youtube/kn picks |
| `kj-controller/routes.py` | Modify | Queue-item fields; `_attempt_sing_fallback`; worker error branch |
| `kj-controller/sing_store.py` | Modify | `mark_unavailable` transition; status enum coverage |
| `kj-controller/sing.py` | Modify | Expose resolution state/label in `_public_request_view` |
| `kj-controller/push_dispatcher.py` | Modify | Targeted terminal-state push (enhancement) |
| `kj-controller/tests/test_sing_fallback*.py` | Create | Integration tests for worker fallback |
| `docs/ARCHITECTURE.md`, `docs/CHANGELOG.md`, `docs/TROUBLESHOOTING.md` | Modify | Document module + flow |

## Testing Strategy

- **Unit:** `classify_error` (incl. incident string, unknown→transient), candidate iteration/caps.
- **Integration:** worker-with-stub scenarios (a)–(d) above; assert request status, source rebind,
  push count, and no-double-download.
- **Manual (pre-merge, local):** run kj-controller locally, submit a request pointing at a known
  private video with a working alternate in `versions[]`; confirm auto-fallback, `/my-requests`
  copy, and KJ rotation flag. `cd kj-controller && pytest --cov`.

## Open Questions

- [ ] Does `push_dispatcher` expose a per-request/subscription send, or only rotation-mutation
      broadcasts? Determines whether step 8 lands in this PR or as a fast follow.
- [ ] Confirm the sing_requests `status` enum should gain `unavailable` (vs reusing `rejected` with
      a reason). Prefer a distinct `unavailable` for singer-facing copy.
- [ ] Candidate cap (3) and transient-retry cap (2) — tune during integration if needed.

## Rollback Plan

- Pure code addition behind the existing worker path; nothing changes for non-sing downloads.
- If fallback misbehaves in production, revert the `routes.py` worker-branch change (and the
  `sing.js` `versions[]` attachment) — the system returns to today's "single attempt, red ❌,
  manual KJ fix" behaviour with no data migration to undo (new queue-item fields are ignored).
- This is a **backend** change → requires `systemctl restart kj-controller`; deploy only in a
  maintenance window, never mid-show.
