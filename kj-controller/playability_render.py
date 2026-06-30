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


def build_mpv_capture_cmd(path, out_dir, start_s, frames=3, fps=1, audio_file=None):
    cmd = [
        "mpv", "--no-config", "--ao=null",
        "--vo=image", "--vo-image-format=png",
        f"--vo-image-outdir={out_dir}",
        f"--start={start_s}", f"--vf=fps={fps}", f"--frames={frames}",
        "--really-quiet",
    ]
    # For CD+G, mpv must be handed the .cdg as the main file with the audio
    # attached as an external track (mirrors production loadfile + audio-add).
    # The audio supplies the timeline so mpv can seek to the mid-file capture
    # point instead of aborting on a bare, timeline-less .cdg.
    if audio_file:
        cmd.append(f"--audio-file={audio_file}")
    cmd.append(path)
    return cmd


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


def _display_in_use(n):
    """True if X display ``:n`` already has a server socket or lock file."""
    return os.path.exists(f"/tmp/.X11-unix/X{n}") or os.path.exists(f"/tmp/.X{n}-lock")


def pick_free_display(in_use=_display_in_use, start=99, limit=199):
    """Return the first free ``:N`` display string at or above ``start``.

    A single hard-coded ``:99`` collides when two render checks (e.g. a tier-2
    background check and a manually-run batch sweep) run at once. Probing for a
    free number keeps each check on its own display.
    """
    for n in range(start, limit + 1):
        if not in_use(n):
            return f":{n}"
    raise RuntimeError(f"no free X display in :{start}..:{limit}")


class XvfbDisplay:
    """On-demand off-screen X display so VLC can render without touching :0.

    ``display=None`` (the default) auto-picks a free display at enter time so
    concurrent checks don't collide; pass an explicit ``":N"`` to force one
    (e.g. a batch that wants a single shared display).
    """

    def __init__(self, display=None, resolution="1280x720x24", ready_timeout=5.0):
        self.display = display
        self._forced = display is not None
        self.resolution = resolution
        self.ready_timeout = ready_timeout
        self._proc = None

    def _resolve_display(self, in_use=_display_in_use):
        """The display to launch on: explicit if forced, else the first free one."""
        if self._forced:
            return self.display
        return pick_free_display(in_use=in_use)

    def __enter__(self):
        # Resolve (auto-pick) here, not in __init__, so the probe sees display
        # state right before launch. On a lost race — the picked display gets
        # grabbed between probe and launch and Xvfb exits early — re-pick the
        # next free one. A forced display is never re-picked.
        last_err = None
        for _ in range(5):
            self.display = self._resolve_display()
            num = self.display.lstrip(":")
            sock = f"/tmp/.X11-unix/X{num}"
            self._proc = subprocess.Popen(
                ["Xvfb", self.display, "-screen", "0", self.resolution,
                 "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + self.ready_timeout
            while time.monotonic() < deadline:
                # Check liveness first: only treat the socket as ready when the
                # server *we* just launched is still alive (a dead proc means
                # the display was taken or failed — re-pick instead of latching
                # onto a competing server's socket).
                if self._proc.poll() is not None:
                    break
                if os.path.exists(sock):
                    return self
                time.sleep(0.1)
            self.__exit__(None, None, None)
            last_err = RuntimeError(f"Xvfb {self.display} not ready/exited early")
            if self._forced:
                break
        raise last_err or RuntimeError("Xvfb failed to start")

    def __exit__(self, *exc):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()  # reap so we don't leak a zombie Xvfb
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
                 capture_timeout=90, keep_dir=None, audio_file=None):
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
            cmd = build_mpv_capture_cmd(path, out_dir, start, audio_file=audio_file)
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
