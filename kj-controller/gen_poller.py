"""GenPoller: background thread polling gen API for active rotation jobs."""

import logging
import threading

from gen_client import GenStatus, map_gen_status

logger = logging.getLogger(__name__)


class GenPoller:
    """Polls gen API for active rotation jobs and auto-downloads completed videos."""

    def __init__(self, gen_client, rotation_manager, media, download_folder, poll_interval=60):
        self.gen_client = gen_client
        self.rotation = rotation_manager
        self.media = media
        self.download_folder = download_folder
        self.poll_interval = poll_interval
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

                if new_status != old_status:
                    self.rotation.set_gen_status(entry["id"], job_id, new_status)
                    logger.info("Gen job %s: %s -> %s", job_id, old_status, new_status)

                if new_status == GenStatus.COMPLETE:
                    self._handle_complete(entry, job_id)

            except Exception as e:
                logger.error("Error polling gen job %s: %s", job_id, e)

    def _handle_complete(self, entry, job_id):
        """Download completed video and link to rotation entry."""
        try:
            download_url = self.gen_client.get_download_url(job_id)
            if not download_url:
                logger.error("No download URL for completed gen job %s", job_id)
                return

            # Build filename: GEN-{job_id} - {artist} - {title}.mp4
            song_artist = entry.get("song_artist", "Unknown")
            # Parse "Title - Artist" format
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
            if file_path:
                self.rotation.complete_gen_job(job_id, file_path)
                logger.info("Gen job %s: downloaded and linked %s", job_id, file_path)
            else:
                logger.error("Gen job %s: download failed", job_id)
        except Exception as e:
            logger.error("Error downloading gen job %s: %s", job_id, e)

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
