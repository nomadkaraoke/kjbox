"""GenPoller: background thread polling gen API for active rotation jobs."""

import logging
import os
import threading

from gen_client import GenStatus, map_gen_status

logger = logging.getLogger(__name__)

# Public NOMAD releases are pushed by gen to the Divebar bucket that master-sync
# mirrors onto the box as "NOMAD-#### - Artist - Title.mp4".
_MASTER_BRAND_PREFIX = "NOMAD-"


class GenPoller:
    """Polls gen API for active rotation jobs and links their finished videos.

    A finished job is linked ONLY via its branded NOMAD-#### master, pulled onto
    the box by master-sync (~60s after gen publishes) — never a separate direct
    download, so the library has exactly one copy of every track (Andrew,
    2026-09-25). ``gen_status=syncing`` shows the KJ it's waiting on the sync;
    ``complete`` is written only once the file is linked.
    """

    def __init__(self, gen_client, rotation_manager, media, download_folder, poll_interval=60):
        self.gen_client = gen_client
        self.rotation = rotation_manager
        self.media = media
        self.download_folder = download_folder
        self.poll_interval = poll_interval
        self._warned = set()   # job ids already logged as un-linkable
        self._stop_event = threading.Event()
        self._thread = None

    def poll_once(self):
        """Check all active gen entries and update their status."""
        active = self.rotation.store.get_active_gen_entries()
        if not active:
            return

        for entry in active:
            job_id = entry["gen_job_id"]
            try:
                job_data = self.gen_client.get_job_status(job_id)
                api_status = job_data.get("status", "")
                new_status = map_gen_status(api_status)
                old_status = entry["gen_status"]

                if new_status == GenStatus.COMPLETE:
                    self._handle_complete(entry, job_id, job_data)
                elif new_status != old_status:
                    self.rotation.set_gen_status(entry["id"], job_id, new_status)
                    logger.info("Gen job %s: %s -> %s", job_id, old_status, new_status)

            except Exception as e:
                logger.error("Error polling gen job %s: %s", job_id, e)

    def _handle_complete(self, entry, job_id, job_data):
        """Link the synced NOMAD master, or keep waiting for master-sync."""
        brand_code = ((job_data.get("state_data") or {}).get("brand_code") or "").strip()
        master = (self.find_master_file(brand_code)
                  if brand_code.startswith(_MASTER_BRAND_PREFIX) else None)
        if master:
            self.rotation.complete_gen_job(job_id, master)
            self._warned.discard(job_id)
            logger.info("Gen job %s: linked master %s", job_id, master)
            return
        if entry.get("gen_status") != GenStatus.SYNCING:
            self.rotation.set_gen_status(entry["id"], job_id, GenStatus.SYNCING)
            logger.info("Gen job %s complete (%s) — waiting for master-sync",
                        job_id, brand_code or "no brand code")
        if not brand_code.startswith(_MASTER_BRAND_PREFIX) and job_id not in self._warned:
            # e.g. a private NOMADNP- track — never pushed to the mirror.
            self._warned.add(job_id)
            logger.warning("Gen job %s has brand %r — no NOMAD master will sync; "
                           "link it by hand", job_id, brand_code)

    def find_master_file(self, brand_code):
        """Path of the indexed master whose filename starts "<brand_code> - "."""
        prefix = f"{brand_code} - "
        for path in list(getattr(self.media, "index", {}) or {}):
            if os.path.basename(path).startswith(prefix) and os.path.exists(path):
                return path
        return None

    def start(self):
        """Start background polling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("GenPoller started (interval: %ds)", self.poll_interval)

    def stop(self):
        """Stop background polling."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("GenPoller stopped")

    def _run(self):
        """Polling loop."""
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as e:
                logger.error("GenPoller error: %s", e)
            self._stop_event.wait(self.poll_interval)
