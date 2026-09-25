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
