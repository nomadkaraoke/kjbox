"""Submit-time karaoke-gen jobs for singer "make" requests.

A singer's make request starts its gen job the moment it is submitted (in a
background thread — gen's audio search can take tens of seconds), so for songs
gen can handle fully automatically the video is often ready by the time the KJ
gets to it. The job id lives on the sing_request until approval creates the
rotation entry; ``attach_on_approve`` then binds it to that entry so the
GenPoller can track it, auto-link the finished NOMAD master and flip the entry
from "Being Made (!)" to "Waiting".

Submission and approval race (auto-approve runs right after submit), so both
sides hand over under one lock:

- approve first  → the entry id is recorded on the request (linked_entry_id)
                    and the worker attaches the job when it arrives;
- worker first   → approval finds gen_job_id and attaches it directly.
"""

import logging
import threading

from gen_client import GenStatus, map_gen_status

logger = logging.getLogger(__name__)

BEING_MADE_STATUS = "Being Made (!)"

SUBMITTING = "submitting"
SUBMITTED = "submitted"
FAILED = "failed"

_handoff_lock = threading.Lock()


def submit_early(app, request_id):
    """Start the gen job for make request ``request_id`` in the background.

    Returns True when a submission was started (gen configured), else False —
    the request then falls back to the approve-time job start.
    """
    gen_client = getattr(app, "gen_client", None)
    store = getattr(app, "sing_store", None)
    if gen_client is None or store is None:
        return False
    req = store.get_request(request_id)
    if req is None:
        return False
    # Capture the song NOW: if the singer changes it before the worker runs,
    # the worker must see its job as stale rather than adopt the new song.
    song = (req.get("song_artist", ""), req.get("song_title", ""))
    store.set_request_gen(request_id, None, SUBMITTING)
    threading.Thread(
        target=_submit_worker, args=(app, request_id, song), daemon=True,
        name=f"make-submit-{request_id}",
    ).start()
    return True


def _submit_worker(app, request_id, song):
    store = app.sing_store
    job_id = None
    status = "pending"
    try:
        result = app.gen_client.create_job(*song)
        job_id = result.get("job_id")
        status = result.get("status") or "pending"
        if not job_id:
            raise RuntimeError("Gen API did not return a job_id")
    except Exception as exc:
        logger.warning("Make request %s: early gen submit failed: %s", request_id, exc)

    with _handoff_lock:
        current = store.get_request(request_id)
        # The singer switched this request to a different song while the job
        # was being created — this job belongs to nobody now. (Compare the
        # song too: a switch to ANOTHER make song re-enters 'submitting'.)
        if (
            current is None
            or current.get("gen_submit_state") != SUBMITTING
            or current.get("source_type") != "make"
            or (current.get("song_artist"), current.get("song_title")) != song
        ):
            logger.info("Make request %s: early job %s superseded; dropping", request_id, job_id)
            return
        if not job_id:
            store.set_request_gen(request_id, None, FAILED)
            return
        store.set_request_gen(request_id, job_id, SUBMITTED)
        entry_id = current.get("linked_entry_id")
        logger.info("Make request %s: gen job %s started", request_id, job_id)
    if entry_id:
        _attach(app, entry_id, job_id, map_gen_status(status))


def reset(app, request_id):
    """Forget any early job (the request no longer asks for this song)."""
    with _handoff_lock:
        app.sing_store.set_request_gen(request_id, None, None)


def attach_on_approve(app, req, entry_id):
    """Bind the request's early gen job to its new rotation entry.

    Returns True when handled — the job was attached, or it is still being
    submitted and the worker will attach it. False means there is no early job
    and the caller must start one itself.
    """
    store = getattr(app, "sing_store", None)
    if store is None:
        return False
    with _handoff_lock:
        current = store.get_request(req["id"]) or {}
        state = current.get("gen_submit_state")
        job_id = current.get("gen_job_id")
        if state == SUBMITTING:
            store.set_linked_entry(req["id"], entry_id)
            return True
        if not (state == SUBMITTED and job_id):
            return False
    _attach(app, entry_id, job_id, GenStatus.PROCESSING)
    return True


def _attach(app, entry_id, job_id, gen_status):
    try:
        app.rotation.set_gen_status(entry_id, job_id, gen_status)
    except Exception:
        logger.exception("Make: attaching gen job %s to entry %s failed", job_id, entry_id)
