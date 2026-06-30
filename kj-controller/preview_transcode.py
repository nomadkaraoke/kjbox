"""On-demand exotic-video -> HLS transcoder for browser preview.

Single active job (a new preview bumps any in-progress one). Runs niced/ioniced so
it yields to the live primary player. Writes a ``.done`` sentinel (via the caller's
``mark_done`` callback) only after ffmpeg exits 0, so a truncated transcode is never
treated as a valid cache entry.
"""
import os
import shutil
import subprocess
import threading
import time


class TranscodeBusy(Exception):
    pass


class TranscodeError(Exception):
    pass


class TranscodeManager:
    def __init__(self, config):
        self.config = config or {}
        self.height = int(self.config.get("preview_transcode_height", 480))
        self.preset = self.config.get("preview_transcode_preset", "veryfast")
        self._lock = threading.Lock()
        self._active = None       # current Popen
        self._active_dest = None  # dest dir of the current Popen (cleaned on kill)

    def _prefix(self):
        pre = []
        if shutil.which("nice"):
            pre += ["nice", "-n", "19"]
        if shutil.which("ionice"):
            pre += ["ionice", "-c3"]
        return pre

    def _cmd(self, source_path, dest_dir):
        return self._prefix() + [
            "ffmpeg", "-nostdin", "-y", "-i", source_path,
            "-vf", f"scale=-2:{self.height}", "-c:v", "libx264",
            "-preset", self.preset, "-profile:v", "main", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "event",
            "-hls_flags", "append_list",
            "-hls_segment_filename", os.path.join(dest_dir, "seg-%d.ts"),
            os.path.join(dest_dir, "index.m3u8"),
        ]

    def _kill_active_locked(self):
        """Kill the active job and delete its partial output. Caller holds _lock."""
        p = self._active
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        if self._active_dest:
            # A killed transcode never gets a `.done` marker, so its partial dir
            # would otherwise be invisible to eviction and leak disk. Remove it.
            shutil.rmtree(self._active_dest, ignore_errors=True)
        self._active = None
        self._active_dest = None

    def kill_active(self):
        with self._lock:
            self._kill_active_locked()

    def ensure_hls(self, source_path, dest_dir, mark_done):
        """Always (re)build a fresh HLS transcode into ``dest_dir``; return its
        ``index.m3u8`` once it first appears.

        The caller (PreviewService) decides cache hits via the cache's ``.done``
        sentinel and only calls this on a miss — so a stale partial playlist from a
        killed job is never reused. Bumping any in-progress job + the spawn + the
        ``_active`` assignment are done atomically under the lock so two concurrent
        callers can't both launch ffmpeg.
        """
        if not shutil.which("ffmpeg"):
            raise TranscodeError("ffmpeg not available")
        playlist = os.path.join(dest_dir, "index.m3u8")
        with self._lock:
            self._kill_active_locked()           # bump any in-progress job
            shutil.rmtree(dest_dir, ignore_errors=True)  # discard stale partial output
            os.makedirs(dest_dir, exist_ok=True)
            proc = subprocess.Popen(
                self._cmd(source_path, dest_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._active = proc
            self._active_dest = dest_dir

        def _watch(p=proc, d=dest_dir):
            rc = p.wait()
            with self._lock:
                # Only act if this job is still the active one — a newer preview may
                # have bumped (killed + cleaned) it, in which case we must not mark a
                # rebuilt/removed dir done.
                if self._active is p:
                    try:
                        if rc == 0:
                            mark_done()            # success: keep dir + .done
                        else:
                            shutil.rmtree(d, ignore_errors=True)  # self-failed: clean
                    except Exception:
                        shutil.rmtree(d, ignore_errors=True)
                    finally:
                        self._active = None
                        self._active_dest = None

        threading.Thread(target=_watch, daemon=True).start()

        deadline = time.time() + 15
        while time.time() < deadline:
            # Check process failure BEFORE playlist existence so a partial playlist
            # written by an already-failing ffmpeg is never returned as usable.
            rc = proc.poll()
            if rc is not None and rc != 0:
                with self._lock:
                    if self._active is proc:
                        shutil.rmtree(dest_dir, ignore_errors=True)
                        self._active = None
                        self._active_dest = None
                raise TranscodeError("ffmpeg exited before producing a playlist")
            if os.path.exists(playlist):
                return playlist
            time.sleep(0.1)
        with self._lock:
            if self._active is proc:
                self._kill_active_locked()
        raise TranscodeError("transcode did not produce a playlist in time")
