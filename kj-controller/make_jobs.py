"""Hand-over of a singer make-it's karaoke-gen job to its rotation entry.

The singer's gen job is created at submit time (``sing_make.create_job``, on
their own verified gen account) and its id stored on the sing_request. When
the request becomes a rotation entry, ``attach_on_approve`` binds that job to
the entry so the GenPoller can track it, link the finished NOMAD master and
flip the entry from "Being Made (!)" to "Waiting".
"""

import logging

from gen_client import GenStatus

logger = logging.getLogger(__name__)


def attach_on_approve(app, req, entry_id):
    """Bind the request's submit-time gen job to its new rotation entry.

    Returns True when a job was attached. False means the request has no job
    (a legacy make request, or one created before gen was reachable) and the
    caller must start one itself.
    """
    store = getattr(app, "sing_store", None)
    if store is None:
        return False
    current = store.get_request(req["id"]) or {}
    job_id = current.get("gen_job_id")
    if not job_id:
        return False
    try:
        app.rotation.set_gen_status(entry_id, job_id, GenStatus.PROCESSING)
    except Exception:
        # The job is real either way — never report failure (the caller would
        # start a duplicate job).
        logger.exception("Make: attaching gen job %s to entry %s failed", job_id, entry_id)
    return True
