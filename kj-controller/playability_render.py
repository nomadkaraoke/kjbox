"""Render capture command builders for playability checks.

Builds mpv / cvlc CLI arg lists used to grab PNG frames from a video file.
Captures mid-file to skip black intros/outros; short/unknown -> start 0.
"""

import glob
import os
import shutil
import subprocess
import time

MID_FILE_FRACTION = 0.4
MIN_DURATION_FOR_MIDFILE = 6.0


def capture_start(duration) -> float:
    if not duration or duration < MIN_DURATION_FOR_MIDFILE:
        return 0.0
    return round(duration * MID_FILE_FRACTION, 3)


def build_mpv_capture_cmd(path, out_dir, start_s, frames=3, fps=1):
    return [
        "mpv", "--no-config", "--ao=null",
        "--vo=image", "--vo-image-format=png",
        f"--vo-image-outdir={out_dir}",
        f"--start={start_s}", f"--vf=fps={fps}", f"--frames={frames}",
        "--really-quiet", path,
    ]


def build_vlc_capture_cmd(path, display, out_dir, start_s, window_s=3, scene_ratio=25):
    return [
        "env", f"DISPLAY={display}",
        "cvlc", "--no-audio", "--vout", "x11",
        "--no-video-title-show",
        "--video-filter=scene", "--scene-format=png",
        f"--scene-path={out_dir}", f"--scene-ratio={scene_ratio}",
        f"--start-time={start_s}", f"--stop-time={start_s + window_s}",
        "--play-and-exit", path, "vlc://quit",
    ]


class XvfbDisplay:
    """On-demand off-screen X display so VLC can render without touching :0."""

    def __init__(self, display=":99", resolution="1280x720x24", ready_timeout=5.0):
        self.display = display
        self.resolution = resolution
        self.ready_timeout = ready_timeout
        self._proc = None

    def __enter__(self):
        num = self.display.lstrip(":")
        sock = f"/tmp/.X11-unix/X{num}"
        self._proc = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", self.resolution, "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if os.path.exists(sock):
                return self
            if self._proc.poll() is not None:
                raise RuntimeError(f"Xvfb {self.display} exited early")
            time.sleep(0.1)
        self.__exit__(None, None, None)
        raise RuntimeError(f"Xvfb {self.display} not ready in {self.ready_timeout}s")

    def __exit__(self, *exc):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


def _save_representative_frame(frames, keep_dir, path, renderer):
    """Copy one representative captured frame to keep_dir for human review.

    Prefers the first non-blank frame (the evidence the verdict relied on);
    falls back to the first captured frame. Returns the saved path or None.
    """
    from frame_analysis import analyze_frame

    if not frames:
        return None
    chosen = next((f for f in frames if not analyze_frame(f).is_blank), frames[0])
    os.makedirs(keep_dir, exist_ok=True)
    safe = os.path.basename(path).replace(os.sep, "_")[:150]
    dest = os.path.join(keep_dir, f"{safe}__{renderer}.png")
    try:
        shutil.copyfile(chosen, dest)
    except OSError:
        return None
    return dest


def render_check(run, path, renderer, duration, display=":99", tmp_root=None,
                 capture_timeout=90, keep_dir=None):
    """Capture frames from `renderer` and judge them. `run(cmd, timeout)` does
    the actual subprocess call (injected so this is unit-testable).

    When `keep_dir` is set, a single representative captured frame is copied
    there (named `<file>__<renderer>.png`) for human review, and its path is
    recorded as `saved_frame` in the returned verdict.
    """
    import tempfile

    from frame_analysis import judge_renderer_frames

    start = capture_start(duration)
    out_dir = tempfile.mkdtemp(prefix=f"kj-cap-{renderer}-", dir=tmp_root)
    error = None
    saved_frame = None
    t0 = time.monotonic()
    try:
        if renderer == "mpv":
            cmd = build_mpv_capture_cmd(path, out_dir, start)
        elif renderer == "vlc":
            cmd = build_vlc_capture_cmd(path, display, out_dir, start)
        else:
            raise ValueError(f"unknown renderer {renderer}")
        rc, _out, err = run(cmd, capture_timeout)
        if rc not in (0, None):
            # Guard against whitespace-only stderr: splitlines() would be empty.
            error = ((err or "").strip().splitlines() or [f"{renderer} exited {rc}"])[-1]
        frames = sorted(glob.glob(os.path.join(out_dir, "*.png")))
        verdict = judge_renderer_frames(frames)
        if keep_dir:
            saved_frame = _save_representative_frame(frames, keep_dir, path, renderer)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    verdict["error"] = error if not verdict.get("frame_nonblank") else None
    verdict["elapsed_s"] = round(time.monotonic() - t0, 3)
    verdict["saved_frame"] = saved_frame
    return verdict
