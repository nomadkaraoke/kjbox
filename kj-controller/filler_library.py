"""Filler-music library: list / label / upload / import-from-YouTube / delete.

Backs the "Manage filler music" modal in the KJ UI. The filler player itself
(`filler.py` / the `/filler_music` routes) only needs audio files in
``filler_music_dir``; everything here is about getting files in and out of that
directory safely.

* Display names live in a sidecar JSON (``.filler-labels.json``) so the on-disk
  filename (what VLC and the saved "current track" state use) never changes
  under the player.
* Anything new is normalised to MP3 with ffmpeg, optionally clipped
  (start + length) and loudness-normalised, written to a hidden temp dir and
  atomically renamed in, so a half-finished import is never listed or played.
* Long work (YouTube download, transcode) runs on a background thread, one at a
  time, with progress the UI polls.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import urlparse

from utils import log_message, sanitize_filename_part

AUDIO_EXTS = ('.mp3', '.wav', '.ogg', '.flac')
LABELS_FILE = '.filler-labels.json'

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # Cloudflare's free-plan request cap
MAX_CLIP_S = 3600
DEFAULT_CLIP_S = 300
MIN_FREE_BYTES = 200 * 1024 * 1024
FFMPEG_TIMEOUT_S = 900

_YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}
_LOUDNORM = 'loudnorm=I=-16:TP=-1.5:LRA=11'
LOOP_FADE_S = 3.0  # crossfade length that blends a track's end into its start


class FillerError(Exception):
    """A user-presentable failure (bad input, ffmpeg/yt-dlp error, no space)."""


# ── Names, labels, paths ───────────────────────────────────────────────

def filler_dir(cfg):
    return cfg.get('filler_music_dir', '') or ''


def slugify_filename(text, fallback='filler'):
    """Filesystem-safe stem from free text (unicode kept, separators/dots stripped)."""
    stem = sanitize_filename_part(text or '').strip(' .')
    return stem or fallback


def unique_name(directory, stem, ext='.mp3'):
    """``stem.ext``, or ``stem-2.ext``/``stem-3.ext``... if it already exists."""
    name = f"{stem}{ext}"
    n = 2
    while os.path.exists(os.path.join(directory, name)):
        name = f"{stem}-{n}{ext}"
        n += 1
    return name


def track_path(directory, name):
    """Absolute path of an existing track, or None for anything not a plain audio filename."""
    if not directory or not name or name != os.path.basename(name) or name.startswith('.'):
        return None
    if not name.lower().endswith(AUDIO_EXTS):
        return None
    path = os.path.join(directory, name)
    return path if os.path.isfile(path) else None


_labels_lock = threading.Lock()


def load_labels(directory):
    try:
        with open(os.path.join(directory, LABELS_FILE)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_labels(directory, labels):
    path = os.path.join(directory, LABELS_FILE)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.labels-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(labels, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def set_label(directory, name, label):
    """Set (or, if blank / equal to the filename, clear) a track's display name."""
    label = (label or '').strip()[:120]
    with _labels_lock:
        labels = load_labels(directory)
        if label and label != name:
            labels[name] = label
        else:
            labels.pop(name, None)
        _write_labels(directory, labels)


def _drop_label(directory, name):
    with _labels_lock:
        labels = load_labels(directory)
        if labels.pop(name, None) is not None:
            _write_labels(directory, labels)


# ── Timestamps ─────────────────────────────────────────────────────────

def parse_timestamp(text):
    """``"90"``, ``"1:30"``, ``"1:02:03"`` or ``"1:30.5"`` -> seconds. Blank -> None."""
    text = (str(text) if text is not None else '').strip()
    if not text:
        return None
    parts = text.split(':')
    if len(parts) > 3 or not all(re.fullmatch(r'\d+(\.\d+)?', p) for p in parts):
        raise FillerError(f"Can't read time '{text}' - use seconds or mm:ss")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def parse_clip(start, duration, default_duration=DEFAULT_CLIP_S):
    """Validated ``(start_s, duration_s)``; ``duration_s`` None means "to the end"."""
    start_s = parse_timestamp(start) or 0.0
    if duration is None or str(duration).strip() == '':
        dur_s = default_duration
    else:
        dur_s = parse_timestamp(duration)
    if dur_s is not None and dur_s <= 0:
        raise FillerError("Length must be greater than zero")
    if dur_s is not None and dur_s > MAX_CLIP_S:
        raise FillerError(f"Length can be at most {MAX_CLIP_S // 60} minutes")
    return start_s, dur_s


def format_hms(seconds):
    seconds = int(round(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── ffmpeg / ffprobe ───────────────────────────────────────────────────

def build_ffmpeg_cmd(src, dest, start=None, duration=None, normalize=False, wav=False):
    """ffmpeg argv converting ``src`` to MP3 (VBR ~190k), dropping any video/cover art.

    ``wav=True`` writes lossless PCM instead (intermediate for the loop pass)."""
    cmd = ['ffmpeg', '-nostdin', '-y', '-v', 'error']
    if start:
        cmd += ['-ss', f"{start:.3f}"]
    cmd += ['-i', src]
    if duration:
        cmd += ['-t', f"{duration:.3f}"]
    cmd += ['-vn', '-map', '0:a:0', '-map_metadata', '-1']
    if normalize:
        cmd += ['-af', _LOUDNORM]
    cmd += ['-codec:a', 'pcm_s16le', '-f', 'wav', dest] if wav else \
        ['-codec:a', 'libmp3lame', '-q:a', '2', '-f', 'mp3', dest]
    return cmd


def convert_to_mp3(src, dest, start=None, duration=None, normalize=False, wav=False):
    cmd = build_ffmpeg_cmd(src, dest, start, duration, normalize, wav)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S)
    except FileNotFoundError:
        raise FillerError("ffmpeg is not installed on this device")
    except subprocess.TimeoutExpired:
        raise FillerError("Audio conversion timed out")
    if proc.returncode != 0 or not os.path.isfile(dest) or os.path.getsize(dest) == 0:
        tail = (proc.stderr or '').strip().splitlines()[-1:] or ['unknown error']
        raise FillerError(f"Couldn't convert that file to MP3 ({tail[0][:160]})")


def build_loop_cmd(src, dest, length, fade=LOOP_FADE_S):
    """ffmpeg argv that makes ``src`` (``length`` s) loop without a hard stop.

    The last ``fade`` seconds are crossfaded into the first ``fade`` seconds and the
    file starts ``fade`` seconds in, so when the player wraps from the end back to the
    start the audio carries on seamlessly. Result is ``length - fade`` seconds.
    Tracks too short for that (< 4x the fade) get a simple fade-in/fade-out instead.
    """
    base = ['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', src]
    out = ['-codec:a', 'libmp3lame', '-q:a', '2', '-f', 'mp3', dest]
    if length < fade * 4:
        f = max(min(length / 4, fade), 0.2)
        return base + ['-af', f"afade=t=in:d={f:.2f},afade=t=out:st={length - f:.3f}:d={f:.2f}"] + out
    graph = (
        f"[0:a]asplit=3[h][m][t];"
        f"[h]atrim=0:{fade},asetpts=PTS-STARTPTS[head];"
        f"[m]atrim={fade}:{length - fade:.3f},asetpts=PTS-STARTPTS[mid];"
        f"[t]atrim={length - fade:.3f}:{length:.3f},asetpts=PTS-STARTPTS[tail];"
        f"[tail][head]acrossfade=d={fade}:c1=qsin:c2=qsin[x];"
        f"[mid][x]concat=n=2:v=0:a=1[out]"
    )
    return base + ['-filter_complex', graph, '-map', '[out]'] + out


def make_loop(src, dest, fade=LOOP_FADE_S):
    length = probe_duration(src)
    if not length:
        raise FillerError("Couldn't read that track's length")
    try:
        proc = subprocess.run(build_loop_cmd(src, dest, length, fade), capture_output=True,
                              text=True, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise FillerError("Loop smoothing timed out")
    if proc.returncode != 0 or not os.path.isfile(dest) or os.path.getsize(dest) == 0:
        tail = (proc.stderr or '').strip().splitlines()[-1:] or ['unknown error']
        raise FillerError(f"Couldn't smooth the loop ({tail[0][:160]})")


def finish_track(src, dest, start, duration, normalize, smooth_loop, workdir, precheck=None):
    """Convert ``src`` to MP3 at ``dest``; with ``smooth_loop`` go via a lossless wav first.

    ``precheck(path)`` sees the converted audio before any loop trimming.
    """
    pre = os.path.join(workdir, 'pre.wav') if smooth_loop else dest
    convert_to_mp3(src, pre, start, duration, normalize, wav=smooth_loop)
    if precheck:
        precheck(pre)
    if smooth_loop:
        make_loop(pre, dest)
        os.remove(pre)


_duration_cache = {}


def probe_duration(path):
    """Track length in seconds (cached by path+mtime), or None if ffprobe can't tell."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key in _duration_cache:
        return _duration_cache[key]
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
            capture_output=True, text=True, timeout=10).stdout.strip()
        value = float(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        value = None
    _duration_cache[key] = value
    return value


# ── Library listing / edits ────────────────────────────────────────────

def list_tracks(cfg, current=None):
    """Tracks as dicts (name, label, size, duration, current), sorted by label."""
    directory = filler_dir(cfg)
    try:
        names = [n for n in os.listdir(directory)
                 if not n.startswith('.') and n.lower().endswith(AUDIO_EXTS)]
    except OSError:
        return []
    labels = load_labels(directory)
    tracks = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        tracks.append({
            'name': name,
            'label': labels.get(name, name),
            'size': size,
            'duration': probe_duration(path),
            'current': name == current,
        })
    tracks.sort(key=lambda t: t['label'].lower())
    return tracks


def free_bytes(directory):
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return None


def check_space(directory):
    free = free_bytes(directory)
    if free is not None and free < MIN_FREE_BYTES:
        raise FillerError(f"Not enough free disk space ({free // (1024 * 1024)} MB left)")


def delete_track(cfg, name, current=None):
    directory = filler_dir(cfg)
    path = track_path(directory, name)
    if not path:
        raise FillerError("Track not found")
    if name == current:
        raise FillerError("That track is the one playing now - pick another filler first")
    os.remove(path)
    _drop_label(directory, name)
    log_message(f"Filler track deleted: {name}", cfg)


def _install(directory, tmp_mp3, label):
    """Atomically move a finished MP3 into the library under a unique, safe name."""
    name = unique_name(directory, slugify_filename(label), '.mp3')
    os.replace(tmp_mp3, os.path.join(directory, name))
    set_label(directory, name, label)
    return name


# ── YouTube ────────────────────────────────────────────────────────────

def validate_youtube_url(url):
    url = (url or '').strip()
    host = (urlparse(url).hostname or '').lower()
    if urlparse(url).scheme not in ('http', 'https') or host not in _YOUTUBE_HOSTS:
        raise FillerError("Paste a youtube.com or youtu.be link")
    return url


def youtube_info(cfg, url):
    import yt_dlp
    from media import _ytdlp_base_opts

    url = validate_youtube_url(url)
    try:
        with yt_dlp.YoutubeDL(_ytdlp_base_opts(cfg)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise FillerError(f"Couldn't read that video ({str(e)[:160]})")
    return {
        'title': info.get('title') or 'YouTube audio',
        'channel': info.get('channel') or info.get('uploader') or '',
        'duration': info.get('duration'),
        'id': info.get('id'),
    }


def _download_youtube_audio(cfg, url, workdir, start_s, dur_s, on_progress):
    """Download (just the wanted section of) the best audio stream. Returns (path, trimmed)."""
    import yt_dlp
    from media import _ytdlp_base_opts
    from yt_dlp.utils import download_range_func

    opts = _ytdlp_base_opts(cfg)
    opts.update({
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(workdir, 'src.%(ext)s'),
        'force_overwrites': True,
        'sleep_interval': 0,
        'max_sleep_interval': 0,
        'sleep_interval_requests': 0,
    })

    def hook(d):
        if d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            if total:
                on_progress(d.get('downloaded_bytes', 0) / total)

    opts['progress_hooks'] = [hook]

    def attempt(options):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
        except Exception as e:
            raise FillerError(f"YouTube download failed ({str(e)[:160]})")
        for f in os.listdir(workdir):
            if f.startswith('src.'):
                return os.path.join(workdir, f)
        raise FillerError("YouTube download produced no audio")

    if start_s or dur_s:
        # Fast path: fetch only the wanted section. YouTube's ffmpeg-based
        # section download is flaky (HTTP errors), so fall back to the whole
        # audio stream and let ffmpeg trim it afterwards.
        end = start_s + dur_s if dur_s else float('inf')
        try:
            return attempt({**opts, 'download_ranges': download_range_func(None, [(start_s, end)])}), True
        except FillerError as e:
            log_message(f"Filler: section download failed, fetching full audio instead ({e})", cfg)
            for f in os.listdir(workdir):
                os.remove(os.path.join(workdir, f))
    return attempt(opts), False


# ── Background jobs ────────────────────────────────────────────────────

class JobManager:
    """Runs one import/convert job at a time on a thread; the UI polls ``get``."""

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._jobs = {}

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _update(self, job_id, **fields):
        with self._lock:
            self._jobs[job_id].update(fields)

    def start(self, kind, work):
        """Run ``work(update)`` on a thread. ``work`` returns the new track name.

        Raises FillerError if another job is still running.
        """
        if not self._busy.acquire(blocking=False):
            raise FillerError("Another filler import is still running - wait for it to finish")
        job_id = uuid.uuid4().hex[:10]
        with self._lock:
            if len(self._jobs) > 20:
                for old in sorted(self._jobs, key=lambda j: self._jobs[j]['created'])[:10]:
                    del self._jobs[old]
            self._jobs[job_id] = {'id': job_id, 'kind': kind, 'state': 'running',
                                  'stage': 'Starting', 'progress': 0.0, 'error': None,
                                  'name': None, 'created': time.time()}

        def run():
            try:
                name = work(lambda **kw: self._update(job_id, **kw))
                self._update(job_id, state='done', stage='Done', progress=1.0, name=name)
            except FillerError as e:
                self._update(job_id, state='error', error=str(e))
            except Exception as e:  # never leave a job stuck "running"
                self._update(job_id, state='error', error=f"Unexpected error: {e}")
            finally:
                self._busy.release()

        threading.Thread(target=run, daemon=True, name=f'filler-{kind}').start()
        return job_id


jobs = JobManager()


def _check_not_truncated(path, video_len, start_s, dur_s):
    """Fail (rather than save a clipped-short track) if YouTube cut the download off."""
    if not video_len:
        return
    expected = min(dur_s or video_len, max(video_len - start_s, 0))
    got = probe_duration(path)
    if expected and got is not None and got < expected * 0.9:
        raise FillerError(
            f"The download came out short ({format_hms(got)} of {format_hms(expected)}) - "
            "YouTube cut it off. Try again.")


def start_youtube_import(cfg, url, label, start, duration, normalize, smooth_loop=False):
    url = validate_youtube_url(url)
    start_s, dur_s = parse_clip(start, duration)
    directory = filler_dir(cfg)
    if not directory:
        raise FillerError("No filler music folder is configured")
    os.makedirs(directory, exist_ok=True)
    check_space(directory)

    def work(update):
        update(stage='Reading video info')
        info = youtube_info(cfg, url)
        label_ = (label or '').strip() or info['title']
        workdir = tempfile.mkdtemp(dir=directory, prefix='.import-')
        try:
            update(stage='Downloading audio', progress=0.0)
            src, trimmed = _download_youtube_audio(
                cfg, url, workdir, start_s, dur_s, lambda p: update(progress=round(p * 0.8, 3)))
            update(stage='Converting to MP3', progress=0.8)
            out = os.path.join(workdir, 'out.mp3')
            # A sectioned download already starts at the clip start; only a full
            # download still needs trimming here.
            finish_track(src, out, None if trimmed else start_s or None, dur_s, normalize,
                         smooth_loop, workdir,
                         precheck=lambda p: _check_not_truncated(p, info.get('duration'), start_s, dur_s))
            name = _install(directory, out, label_)
            log_message(f"Filler track imported from YouTube: {name}", cfg)
            return name
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return jobs.start('youtube', work)


def start_file_import(cfg, upload_path, original_name, label, start, duration, normalize,
                      smooth_loop=False):
    """Convert an already-saved upload (``upload_path``, deleted afterwards) into the library."""
    try:
        start_s, dur_s = parse_clip(start, duration, default_duration=None)
        directory = filler_dir(cfg)
        if not directory:
            raise FillerError("No filler music folder is configured")
        check_space(directory)
    except FillerError:
        os.remove(upload_path)
        raise

    def work(update):
        label_ = (label or '').strip() or os.path.splitext(original_name)[0]
        workdir = tempfile.mkdtemp(dir=directory, prefix='.import-')
        try:
            update(stage='Converting to MP3', progress=0.1)
            out = os.path.join(workdir, 'out.mp3')
            finish_track(upload_path, out, start_s or None, dur_s, normalize, smooth_loop, workdir)
            name = _install(directory, out, label_)
            log_message(f"Filler track uploaded: {name}", cfg)
            return name
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            try:
                os.remove(upload_path)
            except OSError:
                pass

    try:
        return jobs.start('upload', work)
    except FillerError:
        os.remove(upload_path)
        raise


def start_smooth_existing(cfg, name):
    """Make a loop-smoothed copy of an existing track, saved alongside it as "<label> (loop)"."""
    directory = filler_dir(cfg)
    src = track_path(directory, name)
    if not src:
        raise FillerError("Track not found")
    check_space(directory)
    label = f"{load_labels(directory).get(name, os.path.splitext(name)[0])} (loop)"

    def work(update):
        workdir = tempfile.mkdtemp(dir=directory, prefix='.import-')
        try:
            update(stage='Smoothing the loop', progress=0.3)
            out = os.path.join(workdir, 'out.mp3')
            make_loop(src, out)
            new_name = _install(directory, out, label)
            log_message(f"Filler loop-smoothed copy created: {new_name}", cfg)
            return new_name
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return jobs.start('smooth', work)


def cleanup_stale_imports(cfg):
    """Remove ``.import-*`` temp dirs left behind by a restart mid-import."""
    directory = filler_dir(cfg)
    try:
        for n in os.listdir(directory):
            if n.startswith('.import-') or n.startswith('.upload-'):
                p = os.path.join(directory, n)
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
    except OSError:
        pass
