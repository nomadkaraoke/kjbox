"""GenPoller: background thread polling gen API for active rotation jobs."""

import logging
import os
import threading
import time

from gen_client import GenStatus, map_gen_status

logger = logging.getLogger(__name__)

# Public NOMAD releases are pushed by gen to the Divebar bucket that master-sync
# mirrors onto the box as "NOMAD-#### - Artist - Title.mp4". Private tracks
# (NOMADNP-…) are never pushed, so only this prefix is worth waiting for.
_MASTER_BRAND_PREFIX = "NOMAD-"
_MAX_DIRECT_DOWNLOAD_ATTEMPTS = 3


class GenPoller:
    """Polls gen API for active rotation jobs and links their finished videos.

    On completion it prefers the branded NOMAD-#### master that master-sync
    pulls onto the box (the same file every other show uses), showing
    ``gen_status=syncing`` while it waits; after ``master_wait_seconds`` it
    falls back to downloading gen's 720p directly. ``complete`` is only written
    once a file is actually linked, so a failed download is retried.
    """

    def __init__(self, gen_client, rotation_manager, media, download_folder,
                 poll_interval=60, master_wait_seconds=600, clock=time.monotonic):
        self.gen_client = gen_client
        self.rotation = rotation_manager
        self.media = media
        self.download_folder = download_folder
        self.poll_interval = poll_interval
        self.master_wait_seconds = master_wait_seconds
        self._clock = clock
        self._completed_at = {}         # job_id -> clock() when first seen complete
        self._download_attempts = {}    # job_id -> failed direct downloads
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
        """Link the finished video (master first, direct download as fallback)."""
        brand_code = ((job_data.get("state_data") or {}).get("brand_code") or "").strip()
        if self.master_wait_seconds > 0 and brand_code.startswith(_MASTER_BRAND_PREFIX):
            master = self.find_master_file(brand_code)
            if master:
                self._link(job_id, master)
                logger.info("Gen job %s: linked master %s", job_id, master)
                return
            first_seen = self._completed_at.setdefault(job_id, self._clock())
            if self._clock() - first_seen < self.master_wait_seconds:
                if entry.get("gen_status") != GenStatus.SYNCING:
                    self.rotation.set_gen_status(entry["id"], job_id, GenStatus.SYNCING)
                    logger.info("Gen job %s complete (%s) — waiting for master-sync",
                                job_id, brand_code)
                return
            logger.warning("Gen job %s: master %s not synced after %ds — downloading directly",
                           job_id, brand_code, self.master_wait_seconds)
        self._download_direct(entry, job_id)

    def find_master_file(self, brand_code):
        """Path of the indexed master whose filename starts "<brand_code> - "."""
        prefix = f"{brand_code} - "
        for path in list(getattr(self.media, "index", {}) or {}):
            if os.path.basename(path).startswith(prefix) and os.path.exists(path):
                return path
        return None

    def _link(self, job_id, file_path):
        self.rotation.complete_gen_job(job_id, file_path)
        self._completed_at.pop(job_id, None)
        self._download_attempts.pop(job_id, None)

    def _download_direct(self, entry, job_id):
        """Download gen's 720p and link it; give up after a few failures."""
        file_path = None
        try:
            download_url = self.gen_client.get_download_url(job_id)
            if download_url:
                song_artist = entry.get("song_artist", "Unknown")
                # Rotation song text is "Title - Artist"
                parts = song_artist.split(" - ", 1)
                if len(parts) == 2:
                    title, artist = parts
                else:
                    title = song_artist
                    artist = ""
                name_parts = [p for p in [f"GEN-{job_id[:8]}", artist, title] if p]
                filename = " - ".join(name_parts) + ".mp4"
                file_path, _ = self.media.download_from_url(
                    download_url, filename=filename,
                    source="gen", source_ref=job_id[:8], artist=artist, title=title)
            else:
                logger.error("No download URL for completed gen job %s", job_id)
        except Exception as e:
            logger.error("Error downloading gen job %s: %s", job_id, e)

        if file_path:
            self._link(job_id, file_path)
            logger.info("Gen job %s: downloaded and linked %s", job_id, file_path)
            return
        attempts = self._download_attempts.get(job_id, 0) + 1
        self._download_attempts[job_id] = attempts
        if attempts >= _MAX_DIRECT_DOWNLOAD_ATTEMPTS:
            logger.error("Gen job %s: download failed %d times — marking failed",
                         job_id, attempts)
            self.rotation.set_gen_status(entry["id"], job_id, GenStatus.FAILED)
            self._completed_at.pop(job_id, None)
            self._download_attempts.pop(job_id, None)

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
