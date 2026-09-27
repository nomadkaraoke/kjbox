"""GenPoller: background thread polling gen API for active rotation jobs."""

import logging
import os
import tempfile
import threading
import time

from gen_client import GenStatus, map_gen_job, quick_version_info

logger = logging.getLogger(__name__)

# Public NOMAD releases are pushed by gen to the Divebar bucket that master-sync
# mirrors onto the box as "NOMAD-#### - Artist - Title.mp4".
_MASTER_BRAND_PREFIX = "NOMAD-"

# Quick (draft) versions: gen renders a scrolling-lyrics video for kjbox make-it
# jobs a few minutes after the audio lands. While one is on its way we poll
# faster so it reaches the singer within seconds of gen finishing it.
QUICK_FAST_POLL_SECONDS = 10
QUICK_MAX_ATTEMPTS = 3
QUICK_LABEL = "QUICK"
QUICK_NOTE = ("Quick draft version (scrolling lyrics, no word highlighting) made by gen "
              "for a singer's make-it request. The full NOMAD version replaces it when ready.")
# gen-side quick_version states that mean "it's coming".
_QUICK_IN_FLIGHT = ("separating", "rendering")


class GenPoller:
    """Polls gen API for active rotation jobs and links their finished videos.

    A finished job is linked ONLY via its branded NOMAD-#### master, pulled onto
    the box by master-sync (~60s after gen publishes) — never a separate direct
    download, so the library has exactly one copy of every track (Andrew,
    2026-09-25). ``gen_status=syncing`` shows the KJ it's waiting on the sync;
    ``complete`` is written only once the file is linked.
    """

    def __init__(self, gen_client, rotation_manager, media, download_folder, poll_interval=60,
                 quick_enabled=True, stats=None, sing_store=None,
                 fast_poll_interval=QUICK_FAST_POLL_SECONDS):
        self.gen_client = gen_client
        self.rotation = rotation_manager
        self.media = media
        self.download_folder = download_folder
        self.poll_interval = poll_interval
        self.fast_poll_interval = min(fast_poll_interval, poll_interval)
        self.quick_enabled = quick_enabled
        self.stats = stats            # StatsStore — labels imported quick versions
        self.sing_store = sing_store  # SingStore — finds the singer to notify
        self._fast = False            # a quick version is on its way → poll faster
        self._warned = set()   # job ids already logged as un-linkable
        self._stop_event = threading.Event()
        self._thread = None

    def poll_once(self):
        """Check all active gen entries and update their status."""
        active = self.rotation.store.get_active_gen_entries()
        fast = False
        for entry in active:
            job_id = entry["gen_job_id"]
            try:
                job_data = self.gen_client.get_job_status(job_id)
                new_status = map_gen_job(job_data)
                old_status = entry["gen_status"]

                if self.quick_enabled:
                    try:
                        fast = self._sync_quick(entry, job_id, job_data, new_status) or fast
                    except Exception as e:
                        logger.error("Quick version sync failed for %s: %s", job_id, e)

                if new_status == GenStatus.COMPLETE:
                    self._handle_complete(entry, job_id, job_data)
                elif new_status != old_status:
                    self.rotation.set_gen_status(entry["id"], job_id, new_status)
                    logger.info("Gen job %s: %s -> %s", job_id, old_status, new_status)

            except Exception as e:
                logger.error("Error polling gen job %s: %s", job_id, e)
        self._fast = fast

    # ------------------------------------------------------------------
    # Quick (draft) versions
    # ------------------------------------------------------------------

    def _sync_quick(self, entry, job_id, job_data, new_status):
        """Pull gen's quick version onto the box when it's ready.

        Returns True while one is still on its way (→ fast polling)."""
        store = self.rotation.store
        rec = store.get_quick_version(job_id)
        if rec and rec["status"] in ("ready", "chosen", "upgraded"):
            return False
        # A leftover "downloading" row means the app restarted mid-download (polls
        # are sequential on one thread) — retry it like a failure.
        if (rec and rec["status"] in ("failed", "downloading")
                and (rec.get("attempts") or 0) >= QUICK_MAX_ATTEMPTS):
            return False
        # Already has a file (KJ linked one, or the full version landed) or the
        # job is over — a draft is pointless now.
        if entry.get("file_path") or new_status in GenStatus.TERMINAL or new_status == GenStatus.SYNCING:
            return False
        info = quick_version_info(job_data)
        if info.get("available"):
            self._fetch_quick(entry, job_id, job_data, info, rec)
            rec = store.get_quick_version(job_id)
            return bool(rec and rec["status"] == "failed"
                        and (rec.get("attempts") or 0) < QUICK_MAX_ATTEMPTS)
        if info.get("status") in _QUICK_IN_FLIGHT:
            return True
        # gen hasn't started it yet — only worth fast-polling for a singer
        # make-it early in processing (KJ-started jobs never get one).
        return (not info.get("status") and new_status == GenStatus.PROCESSING
                and self._singer_request(job_id) is not None)

    def _singer_request(self, job_id):
        if self.sing_store is None:
            return None
        try:
            return self.sing_store.get_request_by_gen_job_id(job_id)
        except Exception:
            return None

    def _fetch_quick(self, entry, job_id, job_data, info, rec):
        store = self.rotation.store
        attempts = ((rec or {}).get("attempts") or 0) + 1
        store.upsert_quick_version(job_id, status="downloading", attempts=attempts, error=None)
        folder = self.download_folder or os.path.expanduser("~/kjdata/videos")
        os.makedirs(folder, exist_ok=True)
        fd, staging = tempfile.mkstemp(prefix=".quick_staging_", suffix=".mp4", dir=folder)
        os.close(fd)
        artist = (job_data.get("artist") or "").strip()
        title = (job_data.get("title") or "").strip()
        try:
            size = self.gen_client.download_quick_version(job_id, staging)
            result = self.media.import_upload(
                staging, raw_name=f"{artist} - {title} (Quick Version).mp4", ext=".mp4",
                artist_hint=artist or None, title_hint=title or None)
            if self.stats is not None and result.get("media_id"):
                try:
                    self.stats.upsert_note(result["media_id"], QUICK_NOTE, QUICK_LABEL,
                                           artist=artist or None, title=title or None)
                except Exception as e:
                    logger.warning("Could not label quick version %s: %s", result["media_id"], e)
            store.upsert_quick_version(
                job_id, status="ready", file_path=result["path"],
                media_id=result.get("media_id"), lyrics_tier=info.get("lyrics_tier"))
            logger.info("Gen job %s: quick version on the box (%d bytes) → %s",
                        job_id, size, result["path"])
        except Exception as e:
            try:
                if os.path.exists(staging):
                    os.remove(staging)
            except OSError:
                pass
            store.upsert_quick_version(job_id, status="failed", error=str(e)[:500])
            logger.warning("Gen job %s: quick version download failed (attempt %d): %s",
                           job_id, attempts, e)
            return
        self._notify_quick_ready(job_id)
        try:
            self.rotation.notify_changed()
        except Exception as e:
            logger.warning("Rotation refresh after quick version failed: %s", e)

    def _notify_quick_ready(self, job_id):
        req = self._singer_request(job_id)
        dispatcher = getattr(self.rotation, "push_dispatcher", None)
        if req is None or dispatcher is None:
            return
        try:
            dispatcher.notify_request_decision(req["id"], "quick_ready", req)
            self.rotation.store.upsert_quick_version(
                job_id, notified_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:
            logger.warning("quick_ready push failed for %s: %s", job_id, e)

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
            self._stop_event.wait(self.fast_poll_interval if self._fast else self.poll_interval)
