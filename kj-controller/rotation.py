"""RotationManager: thin coordinator for rotation data.

Delegates storage to RotationStore (SQLite) and optional background
backup to SheetSync (Google Sheets). Writes a local JSON display cache
after every mutation for the conky overlay.
"""

import json
import os
import time

from rotation_store import RotationStore

# Local file cache for conky rotation display (avoids sheet polling delay)
ROTATION_CACHE_FILE = "/tmp/rotation_cache.json"

# Re-export _find_header so existing tests that import it from rotation still work
from rotation_sync import _find_header  # noqa: F401  (public re-export)


class RotationManager:
    """Coordinator for karaoke singer rotation.

    Owns a RotationStore for all persistence and an optional SheetSync
    for background Google Sheets backup.

    Attributes:
        store: The RotationStore instance.
        sync:  The SheetSync instance, or None if not configured.
        media: MediaIndex instance (set by app.py), used for duration lookup.
    """

    def __init__(self, db_path, sheet_id=None, credentials_file=None, sync_interval=30):
        self.store = RotationStore(db_path)
        self.sync = None
        self.media = None  # Set by app.py if MediaIndex is available
        self.push_dispatcher = None  # Set by app.py if Web Push is configured

        if sheet_id and credentials_file:
            from rotation_sync import SheetSync
            self.sync = SheetSync(
                self.store,
                sheet_id,
                os.path.expanduser(credentials_file),
                sync_interval,
            )
            self.sync.start()

    # ------------------------------------------------------------------
    # Query methods (no mutation — no cache write needed)
    # ------------------------------------------------------------------

    def get_rotation(self, force_refresh=False):  # noqa: ARG002
        """Return the current rotation queue (non-done entries, ordered by position).

        force_refresh is accepted for API compatibility but ignored; SQLite
        always returns fresh data. Also refreshes the display cache so the
        conky overlay stays current even when no mutations occur.
        """
        entries = self.store.get_entries()
        self._write_display_cache()
        return entries

    def get_stats(self):
        """Return rotation statistics dict (singers, sung, queued, started)."""
        return self.store.get_stats()

    def get_sync_status(self):
        """Return sync status dict.

        If SheetSync is not configured, returns a default offline dict.
        """
        if self.sync is not None:
            return self.sync.get_status()
        return {"last_sync": None, "is_online": False, "next_sync_in": None}

    # ------------------------------------------------------------------
    # Mutation methods — each calls _after_mutation()
    # ------------------------------------------------------------------

    def add_entry(self, singer, song_artist='', notes='', file_path=None, duration=None, singers=None):
        """Add a new singer entry and return the entry dict."""
        if file_path and duration is None:
            duration = self._lookup_duration(file_path)
        result = self.store.add_entry(singer, song_artist, notes, file_path=file_path, duration=duration, singers=singers)
        self._after_mutation()
        return result

    def update_entry(self, entry_id, singer=None, song_artist=None, singers=None):
        """Update singer name and/or song for entry_id. Returns updated entry."""
        result = self.store.update_entry(entry_id, singer=singer, song_artist=song_artist, singers=singers)
        self._after_mutation()
        return result

    def update_status(self, entry_id, new_status):
        """Update status for entry_id (with exclusivity rules for singing/up-next)."""
        self.store.update_status(entry_id, new_status)
        self._after_mutation()

    def mark_singing(self, entry_id):
        """Mark entry as 'Now Singing', clearing any other singing entries."""
        self.update_status(entry_id, "Now Singing")

    def mark_up_next(self, entry_id):
        """Mark entry as 'Up Next', clearing any other up-next entries."""
        self.update_status(entry_id, "Up Next")

    def delete_entry(self, entry_id):
        """Delete entry_id and recompact positions."""
        self.store.delete_entry(entry_id)
        self._after_mutation()

    def move_entry(self, entry_id, new_position):
        """Move entry_id to new_position, shifting surrounding entries."""
        self.store.move_entry(entry_id, new_position)
        self._after_mutation()

    def link_file(self, entry_id, file_path):
        """Link a file to entry_id, auto-looking up duration from self.media."""
        duration = None
        if self.media is not None:
            entry = self.store.get_entry(entry_id)
            if entry is not None:
                media_entry = self.media.index.get(file_path)
                if media_entry is not None:
                    duration = media_entry.get("duration")
        self.store.link_file(entry_id, file_path, duration)
        self._after_mutation()

    def unlink_file(self, entry_id):
        """Remove any linked file from entry_id."""
        self.store.unlink_file(entry_id)
        self._after_mutation()

    def set_download_status(self, entry_id, source, status, download_id=None):
        """Set download tracking fields on a rotation entry."""
        entry = self.store.set_download_status(entry_id, source, status, download_id)
        self._after_mutation()
        return entry

    def set_url_fallback(self, entry_id, url):
        """Set a URL fallback for browser mode playback."""
        entry = self.store.set_url_fallback(entry_id, url)
        self._after_mutation()
        return entry

    def complete_download(self, download_id, file_path):
        """Called by download worker when a rotation-linked download completes."""
        entry = self.store.get_entry_by_download_id(download_id)
        if entry is None:
            return None
        self.store.link_file(entry["id"], file_path, self._lookup_duration(file_path))
        self.store.set_download_status(entry["id"], entry["download_source"], "complete")
        self._after_mutation()
        return self.store.get_entry(entry["id"])

    def _lookup_duration(self, file_path):
        """Look up duration from media index, if available."""
        if self.media is not None and hasattr(self.media, 'index'):
            media_entry = self.media.index.get(file_path)
            if media_entry is not None:
                return media_entry.get("duration")
        return None

    def set_gen_status(self, entry_id, job_id, status):
        """Set gen job tracking fields on a rotation entry."""
        entry = self.store.set_gen_status(entry_id, job_id, status)
        self._after_mutation()
        return entry

    def set_paid(self, entry_id, paid):
        """Set paid priority flag on a rotation entry."""
        entry = self.store.set_paid(entry_id, paid)
        self._after_mutation()
        return entry

    def complete_gen_job(self, job_id, file_path):
        """Called by gen poller when a gen job completes and file is downloaded."""
        entry = self.store.get_entry_by_gen_job_id(job_id)
        if entry is None:
            return None
        self.store.link_file(entry["id"], file_path, self._lookup_duration(file_path))
        self.store.set_gen_status(entry["id"], job_id, "complete")
        self._after_mutation()
        return self.store.get_entry(entry["id"])

    def archive_rotation(self):
        """Archive all current entries and reset the rotation.

        Returns the number of entries archived.
        """
        count = self.store.archive()
        self._after_mutation()
        return count

    def restore_from_sheet(self):
        """Restore rotation from Google Sheet (emergency recovery).

        Raises RuntimeError if SheetSync is not configured.
        Returns number of entries imported.
        """
        if self.sync is None:
            raise RuntimeError("SheetSync is not configured; cannot restore from sheet")
        count = self.sync.restore_from_sheet()
        self._after_mutation()
        return count

    def restore_entries(self, entries):
        """Atomically replace rotation with a snapshot (undo/redo support)."""
        self.store.restore_entries(entries)
        self._after_mutation()

    def get_singer_stats(self):
        """Return per-singer aggregate stats."""
        return self.store.get_singer_stats()

    def rename_singer(self, old_name, new_name):
        """Rename a singer across all entries."""
        self.store.rename_singer(old_name, new_name)
        self._after_mutation()

    def merge_singers(self, source_name, target_name):
        """Merge source singer into target across all entries."""
        self.store.merge_singers(source_name, target_name)
        self._after_mutation()

    def set_singer_status(self, name, new_status):
        """Set status on all non-done entries for a singer."""
        self.store.set_singer_status(name, new_status)
        self._after_mutation()

    def mark_singer_left(self, name):
        """Mark a singer as having left (session-scoped meta flag)."""
        self.store.mark_singer_left(name)
        self._after_mutation()

    def unmark_singer_left(self, name):
        """Remove a singer from the left set."""
        self.store.unmark_singer_left(name)
        self._after_mutation()

    def split_singer(self, source_name, new_name, entry_ids):
        """Reassign specific entries from source_name to new_name."""
        self.store.split_singer(source_name, new_name, entry_ids)
        self._after_mutation()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _after_mutation(self):
        """Write display cache, trigger sync cycle, and notify push dispatcher."""
        self._write_display_cache()
        if self.push_dispatcher is not None:
            try:
                self.push_dispatcher.notify_rotation_changed()
            except Exception:
                # Push is non-critical — never let a dispatcher bug block rotation ops.
                import logging
                logging.getLogger(__name__).exception("push dispatch notify failed")
        if self.sync is not None:
            # Fire-and-forget: sync runs in its own daemon thread already.
            # Calling sync_now() here would block; instead, the background
            # thread will pick up the change within sync_interval seconds.
            # For an immediate push, callers can call self.sync.sync_now().
            pass

    def _write_display_cache(self):
        """Write current rotation to a local JSON file for the conky display.

        Uses an atomic tmp-file + os.replace write to avoid partial reads.
        Silently swallows all errors (best-effort).
        """
        try:
            entries = self.store.get_entries()
            queue = [
                {
                    "singer": e["singer"],
                    "song_artist": e["song_artist"],
                    "status": e["status"],
                    "paid": bool(e["paid"]),
                }
                for e in entries
            ]
            stats = self.store.get_stats()
            data = {
                "queue": queue,
                "stats": stats,
                "updated": time.time(),
            }
            tmp = ROTATION_CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, ROTATION_CACHE_FILE)
        except Exception:
            pass  # Best-effort; display will fall back to sheet polling
