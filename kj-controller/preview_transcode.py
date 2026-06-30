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
        self._active = None  # current Popen

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

    def kill_active(self):
        with self._lock:
            p = self._active
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass
            self._active = None

    def ensure_hls(self, source_path, dest_dir, mark_done):
        """Start (or reuse) an HLS transcode; return path to ``index.m3u8``.

        If the playlist already exists, returns it without launching ffmpeg. Otherwise
        bumps any in-progress job, launches ffmpeg, and blocks only until the playlist
        file first appears (segments keep filling in the background).
        """
        playlist = os.path.join(dest_dir, "index.m3u8")
        if os.path.exists(playlist):
            return playlist
        self.kill_active()  # bump any in-progress job — one preview at a time
        os.makedirs(dest_dir, exist_ok=True)
        if not shutil.which("ffmpeg"):
            raise TranscodeError("ffmpeg not available")
        proc = subprocess.Popen(
            self._cmd(source_path, dest_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with self._lock:
            self._active = proc

        def _watch(p=proc):
            rc = p.wait()
            if rc == 0:
                try:
                    mark_done()
                except Exception:
                    pass

        threading.Thread(target=_watch, daemon=True).start()

        deadline = time.time() + 15
        while time.time() < deadline:
            if os.path.exists(playlist):
                return playlist
            if proc.poll() is not None and proc.returncode != 0:
                raise TranscodeError("ffmpeg exited before producing a playlist")
            time.sleep(0.1)
        raise TranscodeError("transcode did not produce a playlist in time")
