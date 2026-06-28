"""Render capture command builders for playability checks.

Builds mpv / cvlc CLI arg lists used to grab PNG frames from a video file.
Captures mid-file to skip black intros/outros; short/unknown -> start 0.
"""

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
