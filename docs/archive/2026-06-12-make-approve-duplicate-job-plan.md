# Fix: MAKE-request approval leaves request stuck pending + duplicates gen jobs

**Date:** 2026-06-12
**Branch:** `feat/sess-20260612-0140-approve-duplicate-job`
**Area:** kjbox `kj-controller` — sing-request approval (`make` source type)

## Symptom (reported)

Clicking **Approve** on a "made for you" (MAKE) pending request submits a job to
karaoke-gen but doesn't finish the approval / add it to the rotation. Re-clicking
Approve submits a **duplicate** karaoke-gen job.

## Root cause (confirmed with prod evidence)

`approve_sing_request()` in `routes.py` (the `source_type == "make"` branch,
~L3738) is **non-atomic and non-idempotent**:

1. It calls `rotation.add_entry(...)` first, then `gen_client.create_job(...)`,
   which POSTs to gen's `POST /api/audio-search/search?auto_download=true`.
2. That gen endpoint **creates the job early** (`audio_search.py:713`), *then*
   runs the flaky bits: distribution-credential checks + flacfetch audio search.
3. When those fail transiently, gen calls `fail_job(...)` but the job already
   exists, and returns **4xx/5xx**. Confirmed in gen logs at the failure
   timestamps: `404 no_results` (flacfetch found nothing) and Dropbox
   `AuthError('expired_access_token')`.
4. kjbox's `create_job` does `resp.raise_for_status()` → raises → the route's
   `except Exception` returns **500** without `mark_approved()` and without
   removing the rotation entry it already added.

Net effect: request stays `pending` (approval "doesn't finish"), an orphan
rotation entry is left, and **every re-click spawns a new gen job** (no dedup).
Prod log for request #192: `500` @01:02:17, `500` @01:02:20, then `200`
@01:12:12 once the transient gen issue cleared.

## Chosen behavior (per Andrew)

> Add to rotation now, gen in background — but **don't auto-retry**. If the make
> request fails, the rotation entry should still be there (set to the
> "MAKING" / "Being Made" status, like the rotation row's status button) but
> **unlinked** (no gen job).

## Design

Rework **only the `make` branch** of `approve_sing_request()` (other source
types keep their current resolve-first/raise-on-failure behavior, which is
correct for them). Make approval of a MAKE request **always succeed** so the
request leaves `pending` and can never be double-submitted:

1. `entry = rotation.add_entry(...)` — singer is queued immediately.
2. Try to start the gen job:
   - **Success** (got `job_id`): `rotation.set_gen_status(entry, job_id, <mapped>)`.
     Row shows the normal `MAKING` prep badge (gen_status=processing). If the
     *link write* itself fails after a job exists, log it but do **not** treat
     as a failure (the job is real — never re-make → no duplicate).
   - **Failure** (gen raised, or no `job_id`): log a warning, leave the entry
     **unlinked** (no `gen_job_id`) and set its status to **`"Being Made (!)"`**
     via `rotation.update_status(entry_id, ...)`. Row shows `MAKING` status
     badge + `UNLINKED` prep badge. The KJ can start generation later with the
     existing rotation **make** button (`POST /rotation/make`).
3. Return `entry_id` in all cases → route calls `mark_approved()` →
   re-clicking Approve now returns `409` (already approved). **No duplicate gen
   jobs, ever.**

Keep raising for the genuine misconfig case (`gen_client is None`) — that's
checked *before* `add_entry`, so no orphan entry results.

### Why this kills all three pains
- "Approval doesn't finish" → approval always completes (request → approved).
- "Re-click duplicates the job" → impossible; request is no longer pending.
- Orphan rotation entry → now intentional and clearly labeled (`Being Made` +
  `UNLINKED`).

### Scope notes / out of scope
- **Gen-side orphan FAILED jobs:** each *single* failed approve still leaves one
  failed job on gen's side (gen creates the job before searching). With this fix
  the KJ approves once (no re-click storm), so it's 1 instead of N. Fully
  eliminating it needs a gen-side change (don't persist a job when the search
  fails) — separate optional follow-up, not in this PR.
- **`create_job` timeout:** gen's auto-download search is synchronous and can be
  slow; kjbox's `GenClient.REQUEST_TIMEOUT` is 30s. Observed failures were fast
  (not timeouts), but a slow gen response would now be (mis)classified as
  "Being Made unlinked." Optional: bump the create-job timeout. Minor; flag only.

## Files to change

- `kj-controller/routes.py` — rewrite the `make` branch of `approve_sing_request()`.
- `kj-controller/tests/integration/test_sing_admin_routes.py` — add tests.

## Tests (TDD — write first)

1. `test_approve_make_gen_failure_keeps_entry_being_made_unlinked`: `create_job`
   raises → `200`, request `approved`, entry exists with status `"Being Made (!)"`,
   `gen_job_id is None`.
2. `test_approve_make_gen_failure_no_duplicate_on_retry`: `create_job` raises on
   first approve; second approve returns `409` and `create_job` is called
   **exactly once**.
3. `test_approve_make_no_job_id_treated_as_failure`: `create_job` returns
   `{"status": "pending"}` (no `job_id`) → `200`, Being Made + unlinked.
4. Keep/verify `test_approve_make_creates_gen_job` (success path unchanged:
   linked, gen_status set).

Run: `cd kj-controller && pytest tests/integration/test_sing_admin_routes.py -q`
