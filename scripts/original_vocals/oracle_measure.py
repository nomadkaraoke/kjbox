"""Separate one audio file with cheap VR model, measure vocals stem's MEAN volume (dBFS).
Mean, not peak: peak fooled by transient spikes.
"""
import glob
import os
import re
import shutil
import subprocess
import tempfile


def parse_mean_volume(stderr_text: str) -> float | None:
    """Parse ffmpeg volumedetect filter output, extract mean_volume dB.

    Args:
        stderr_text: Raw stderr from ffmpeg volumedetect filter.

    Returns:
        Mean volume in dBFS, or None if not found.
    """
    match = re.search(r'mean_volume:\s*([-\d.]+)\s*dB', stderr_text)
    if match:
        return float(match.group(1))
    return None


def _measure_file_mean_db(audio_path: str) -> float | None:
    """Measure mean volume of audio file via ffmpeg volumedetect.

    Args:
        audio_path: Path to audio file (flac, mp3, etc).

    Returns:
        Mean volume in dBFS, or None on failure.
    """
    r = subprocess.run(
        ['ffmpeg', '-i', audio_path, '-filter:a', 'volumedetect', '-f', 'null', '/dev/null'],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return None
    return parse_mean_volume(r.stderr)


def separate_and_measure(audio_path: str, workdir: str, sep_bin: str, model: str) -> float | None:
    """Separate audio via audio-separator, measure vocals stem MEAN volume.

    Args:
        audio_path: Path to input audio file.
        workdir: Temp directory for separator output.
        sep_bin: Path to audio-separator binary.
        model: Model filename (e.g. '2_HP-UVR.pth').

    Returns:
        Mean volume (dBFS) of vocals stem, or None on failure.
    """
    out = tempfile.mkdtemp(dir=workdir, prefix='ov_')

    env = dict(os.environ)
    env.setdefault("AUDIO_SEPARATOR_MODEL_DIR",
                   "/Volumes/AndrewMacSD/python-audio-separator-models-repo")

    r = subprocess.run(
        [sep_bin, audio_path, "--model_filename", model,
         "--output_dir", out, "--output_format", "flac"],
        capture_output=True, text=True, env=env
    )

    if r.returncode != 0:
        return None

    voc = None
    for f in glob.glob(os.path.join(out, "*.flac")):
        if "(vocals)" in os.path.basename(f).lower():
            voc = f
            break

    if not voc:
        return None

    db = _measure_file_mean_db(voc)
    shutil.rmtree(out, ignore_errors=True)
    return db
