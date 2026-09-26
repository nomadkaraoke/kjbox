# Singer "Make it" → fully-automatic gen job → auto-linked NOMAD master (plan)

## Spec (Andrew, verbatim)

> i believe the singer UI currently doesn't actually handle the "make it" requests very well, basically
> it just has a button which takes them out to gen.nomadkaraoke.com which is kinda unintuitive for
> singers - ideally the same way they search for and add songs should allow them to easily submit a
> "make it" request, basically integrating a simplified version of the karaoke-gen job submission flow
> into the kjbox singer UI so the job gets fully submitted to karaoke-gen the moment the singer submits
> the request. then once the job completes and the NOMAD-720p video gets auto-downloaded to the kjbox,
> it can be auto-linked to the right rotation entry and the rotation entry status changed from Being
> Made to Waiting. that way for "easy" songs which karaoke-gen is able to handle fully automatically,
> in theory the whole flow end to end from singer requesting song through to it being created and
> linked to their rotation entry can hopefully be fully auto.

## What already existed

- Singer empty-search triage: (1) paste YouTube, (2) "Ask the host to make it tonight" (`source_type=make`,
  gated by `sing_accept_make_requests`), (3) "Make it yourself" → external link to gen.nomadkaraoke.com.
- A make request only started a gen job when the KJ **approved** it (`approve_sing_request` make branch →
  `gen_client.create_job` = `POST /api/audio-search/search {auto_download: true}` with the admin token).
- `GenPoller` polled active entries; on complete it downloaded gen's 720p directly into `downloads/gen/`
  and linked it — but set `gen_status=complete` *before* downloading (a failed download never retried),
  never touched the NOMAD-#### master that master-sync pulls every 60s, and never changed the rotation
  status.

## Changes

1. **Submit-time gen job.** `/sing/submit` (and `/sing/requests/<id>/change` to a make) starts the gen job
   in a background thread (`make_jobs.submit_early`). The request row stores `gen_job_id` +
   `gen_submit_state` (`submitting|submitted|failed`). Per-device cap per night
   (`sing_make_max_per_device`, default 3) guards against spam.
2. **Approval attaches, never duplicates.** The make branch of `approve_sing_request` creates the entry with
   status **Being Made (!)** and calls `make_jobs.attach_on_approve` under a lock: attach the existing job,
   or (still submitting) record the entry so the worker attaches when the job id arrives. Only when no
   early job exists (gen unconfigured at submit, early submit failed, legacy request) does it fall back to
   the old synchronous `create_job`.
3. **Master-first completion.** On gen `complete`, the poller reads `state_data.brand_code`. Public
   `NOMAD-####` jobs are pushed to the Divebar bucket by gen; master-sync pulls them within ~60s. The
   poller shows `gen_status=syncing` and links the `NOMAD-#### - …` file from the media index as soon as it
   appears. After `gen_master_wait_seconds` (default 600) it falls back to the direct 720p download.
   `complete` is only written once a file is actually linked; the direct download is retried up to 3 times.
4. **Being Made → Waiting.** `complete_gen_job` flips `Being Made (!)` to `Waiting` when it links the file
   (only from that status — never clobbers Now Singing/On Hold etc.).
5. **Singer UI.** The make card becomes a first-class "We'll make it for you" option (no confirm() popup —
   it goes through the normal confirm screen); the external gen.nomadkaraoke.com DIY card is removed; a
   "Can't find the right one? Make it" link sits under non-empty results too. "My songs" shows make
   progress (being made / needs a quick check by the host / ready).
6. **KJ UI.** `SYNCING` prep badge for the master-wait window.

## Out of scope / notes

- Rejected or cancelled make requests leave their gen job running (it still produces a catalog track).
- Gen's `auto_download` uses `select_best`; the stricter `pick_auto_selection` (KaraokeHunt intake) would
  need a gen-side endpoint change.
- Songs gen can't auto-approve stop at `awaiting_review` — the KJ's NEEDS REVIEW badge opens the review.

---

## Revision 2 (2026-09-25) — Andrew's follow-up + decisions

> 1) i want to make sure we replicate (ideally reusing to avoid duplicate code) the critical parts of the
> karaoke-gen job submission experience for singers, eg.
>  1.A) if they type an artist/title in a lazy way, the system which karaoke-gen has to detect and
>  auto-correct the artist/title should definitely be integrated
>  1.B) singers should choose the input audio using the usual flow which allows users to handle this in
>  karaoke-gen, eg. it searches for lossless flac based on the artist/title but if it can't find that,
>  offers spotify/youtube fallback options for niche stuff etc.
>  1.C) we shouldn't include the audio editing, private delivery or any customisation options in this,
>  lets keep it to the minimum required to get a good karaoke track produced and published publicly
>
> 2) we should capture and verify the user's email address in order for them to use this mode, and the
> karaoke-gen job should be associated with a real karaoke-gen user. this way we can deliver the usual
> karaoke-gen delivery to the user's email, but also this means we're essentially converting some of my
> in-person karaoke night patrons into real karaoke-gen customers.

Decisions (AskUserQuestion, 2026-09-25):
- **Email verification:** 6-digit emailed code typed into the singer UI (new gen feature); kjbox-originated
  sign-ups bypass gen's 2-per-IP signup cap (kjbox enforces its own cap).
- **Credits:** free at Andrew's shows — kjbox tops up 1 credit per make-it job (quietly, no "credits added"
  email); new singers keep gen's welcome credit.
- **Lyrics review:** either — singer via gen's usual review email, or the KJ via the NEEDS REVIEW badge;
  first wins.
- **Approval:** no reject step for make requests. They go straight into the rotation as Being Made (no
  approval queue even with auto-approve off); a hard review just stays at the bottom un-reviewed. Singer
  cancel does NOT cancel the gen job (they still get their video by email).
- **Linking:** ONLY the NOMAD-#### master from master-sync — no direct-download fallback.

### Gen changes (karaoke-gen)
1. `POST /api/users/auth/email-code` `{email}` → emails a 6-digit code (10-min expiry, 5 attempts);
   `POST /api/users/auth/email-code/verify` `{email, code}` → `{session_token, user, credits_granted}`
   (same account creation + welcome-credit path as magic-link verify). Called by kjbox's server with a
   partner secret header → exempt from the per-IP signup cap, attributed (`signup_source=kjbox`).
2. Partner credit top-up: grant 1 credit to a user without the "credits added" email (partner secret).
3. Review-needed email for these jobs carries a sign-in link (singers never signed in on gen's website).
4. Jobs tagged via `X-Client-Id: kjbox` (request_metadata) for attribution.

### kjbox changes
- Singer make flow (server-proxied to gen with the singer's session token, stored server-side per device):
  email → code → artist/title with gen's match-judge ("Corrected to X — undo" / "Did you mean…?") →
  audio choice mirroring gen's AudioSourceStep (best pick + "see all N other options" + YouTube-link
  fallback; tiering ported from `audio-search-utils.ts`) → create-from-search (public, auto review,
  no audio edit) → rotation entry straight in as Being Made.
- GenPoller: link only the synced NOMAD master; no direct download.
