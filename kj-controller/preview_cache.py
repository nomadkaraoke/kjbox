"""Content-addressed on-disk cache for preview transcodes, GCS blobs, CDG extracts.

A transcode cache entry is only valid if it contains a ``.done`` sentinel, written
after ffmpeg exits 0 — so a truncated/interrupted transcode is never served or
counted as complete (the lesson from the playability work).
"""
import hashlib
import os
import shutil

# Bump to invalidate ALL cached transcodes when ffmpeg settings change.
PARAMS_VERSION = "1"


class PreviewCache:
    def __init__(self, root, max_bytes):
        self.root = root
        self.max_bytes = int(max_bytes)
        for sub in ("transcode", "gcsblob", "cdg"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)

    # ---- keys -----------------------------------------------------------
    def local_key(self, real_path):
        st = os.stat(real_path)
        # st_mtime_ns (not int(st_mtime)) so a same-second replace of equal size
        # still invalidates the cache.
        raw = f"{os.path.realpath(real_path)}|{st.st_size}|{st.st_mtime_ns}|{PARAMS_VERSION}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def gcs_key(self, file_id):
        return hashlib.sha1(f"{file_id}|{PARAMS_VERSION}".encode("utf-8")).hexdigest()

    # ---- dirs -----------------------------------------------------------
    def transcode_dir(self, key):
        return os.path.join(self.root, "transcode", key)

    def cdg_dir(self, key):
        return os.path.join(self.root, "cdg", key)

    def gcsblob_path(self, file_id, name):
        # Hash the (untrusted) file_id into the path and reject any name that
        # isn't a plain basename, so a crafted file_id/name can't escape the
        # cache root via path separators or "..".
        safe_name = os.path.basename(name)
        if safe_name in ("", ".", "..") or safe_name != name:
            raise ValueError("invalid cache filename")
        d = os.path.join(self.root, "gcsblob", self.gcs_key(file_id))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, safe_name)

    # ---- done gating ----------------------------------------------------
    def _done_marker(self, key):
        return os.path.join(self.transcode_dir(key), ".done")

    def is_done(self, key):
        return os.path.exists(self._done_marker(key))

    def mark_done(self, key):
        os.makedirs(self.transcode_dir(key), exist_ok=True)
        open(self._done_marker(key), "w").close()

    # ---- LRU ------------------------------------------------------------
    def touch(self, path):
        try:
            os.utime(path, None)
        except OSError:
            pass

    def _dir_size(self, d):
        total = 0
        for root, _dirs, files in os.walk(d):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    def _evictable_entries(self):
        """All cache entries eligible for eviction, as (atime, dir) tuples.

        Covers transcodes (only those with a ``.done`` marker — in-progress
        transcodes are never evicted), downloaded GCS blobs, and extracted CDG
        dirs, so the size cap applies cache-wide, not just to transcodes.
        """
        entries = []
        tdir = os.path.join(self.root, "transcode")
        for name in os.listdir(tdir):
            d = os.path.join(tdir, name)
            marker = os.path.join(d, ".done")
            if os.path.isdir(d) and os.path.exists(marker):
                entries.append((os.path.getmtime(marker), d))
        for sub in ("gcsblob", "cdg"):
            base = os.path.join(self.root, sub)
            for name in os.listdir(base):
                d = os.path.join(base, name)
                if os.path.isdir(d):
                    entries.append((os.path.getmtime(d), d))
        return entries

    def evict_if_needed(self):
        """Delete oldest cache entries (oldest access first) until under the cap."""
        entries = self._evictable_entries()
        total = sum(self._dir_size(d) for _t, d in entries)
        for _t, d in sorted(entries):
            if total <= self.max_bytes:
                break
            sz = self._dir_size(d)
            shutil.rmtree(d, ignore_errors=True)
            total -= sz
