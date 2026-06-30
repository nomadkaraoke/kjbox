"""Browser preview service: resolve a per-source descriptor to a delivery mode and
serve bytes for in-browser audition of any supported file.

Modes: native_video / native_audio (HTTP byte-range), cdg (browser canvas renderer),
hls (capped, cached ffmpeg transcode), youtube (iframe), unavailable (+reason).

Nothing here touches the device's primary player or A/V output — it only reads files
and (for exotic video) runs a niced ffmpeg transcode. See
docs/archive/2026-06-30-browser-preview-playback-design.md.
"""
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid
from urllib.parse import urlparse

import requests

import divebar
import utils
from playability import classify_kind, sibling_cdg_audio
from preview_cache import PreviewCache
from utils import media_type_label
from preview_transcode import TranscodeManager, TranscodeError, TranscodeBusy
from zip_playback import ZipPlayback

logger = logging.getLogger(__name__)

_NATIVE_VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm"}
_NATIVE_VCODECS = {"h264", "vp8", "vp9", "av1"}
_NATIVE_ACODECS = {"aac", "mp3", "opus", "vorbis", None, ""}
_TOKEN_TTL_S = 3600
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                  "youtu.be", "www.youtu.be", "youtube-nocookie.com",
                  "www.youtube-nocookie.com"}


def _is_allowed_youtube_url(url):
    """True only for https YouTube URLs (the iframe consumes this directly)."""
    try:
        p = urlparse(url)
    except (ValueError, TypeError):
        return False
    return p.scheme == "https" and (p.hostname or "").lower() in _YOUTUBE_HOSTS


def _path_inside(candidate, root):
    """True if `candidate` is `root` or strictly within it (no sibling-prefix escape)."""
    try:
        return os.path.commonpath([candidate, root]) == root
    except ValueError:  # different drives / mixed abs+rel
        return False


def parse_range(header, size):
    """Parse an HTTP ``Range`` header into inclusive (start, end) byte offsets.

    Returns None for absent/malformed/unsatisfiable ranges (caller then serves the
    whole file or a 416). Only the first range of a multi-range request is honoured.
    """
    if size <= 0 or not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    a, b = spec.split("-", 1)
    try:
        if a == "":                       # suffix range: last N bytes
            n = int(b)
            if n <= 0:
                return None
            return (max(0, size - n), size - 1)
        start = int(a)
        end = int(b) if b != "" else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size:
        return None
    end = min(end, size - 1)
    if end < start:                       # inverted range, e.g. bytes=10-5
        return None
    return (start, end)


def _is_seg(name):
    return bool(re.fullmatch(r"seg-\d+\.ts", name))


def _ffprobe_codecs(path):
    """Return (video_codec, audio_codec) lowercased, or (None, None) on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=20,
        )
        streams = json.loads(out.stdout or "{}").get("streams", [])
    except Exception:
        return (None, None)
    v = a = None
    for s in streams:
        if s.get("codec_type") == "video" and v is None:
            v = (s.get("codec_name") or "").lower()
        if s.get("codec_type") == "audio" and a is None:
            a = (s.get("codec_name") or "").lower()
    return (v, a)


def _mime_for(path):
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _download_to(url, dst):
    """Stream-download a URL to a local path (atomic via .part rename)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=262144):
                if chunk:
                    fh.write(chunk)
    os.replace(tmp, dst)


class PreviewService:
    """Resolve a preview descriptor to a delivery mode and hold serving tokens.

    A descriptor is one of:
      {"source": "local",   "file_path": ...}
      {"source": "divebar", "file_id": ..., "format": ...}
      {"source": "youtube", "youtube_url": ...}
    plus optional {"title", "prefer_transcode"}.
    """

    def __init__(self, config, media):
        self.config = config or {}
        self.media = media
        root = self.config.get("preview_cache_dir") or os.path.join(
            self.config.get("download_folder", "/tmp"), ".preview-cache")
        self.cache = PreviewCache(root, self.config.get("preview_cache_max_bytes", 8 * 1024 ** 3))
        self.transcoder = TranscodeManager(self.config)
        self._tokens = {}  # token -> entry

    # ---- resolve --------------------------------------------------------
    def resolve(self, descriptor):
        self._gc()
        src = (descriptor or {}).get("source")
        try:
            if src == "youtube":
                url = descriptor.get("youtube_url")
                if not url:
                    return self._unavailable("No YouTube URL")
                if not _is_allowed_youtube_url(url):
                    return self._unavailable("Not a valid YouTube link")
                return {"mode": "youtube", "youtube_url": url,
                        "title": descriptor.get("title", ""), "format": "YouTube", "ext": ""}
            if src == "divebar":
                return self._resolve_divebar(descriptor)
            if src == "local":
                return self._resolve_local_path(descriptor.get("file_path"), descriptor,
                                                title=descriptor.get("title"))
            return self._unavailable("Unknown preview source")
        except TranscodeBusy:
            return self._unavailable("Another preview is transcoding — try again in a moment")
        except TranscodeError:
            logger.exception("preview transcode failed")
            return self._unavailable("Could not prepare this file for preview")
        except Exception:  # never leak a stack trace / path / signed URL to the modal
            logger.exception("preview resolve failed")
            return self._unavailable("Preview failed")

    def _resolve_local_path(self, file_path, descriptor, title=None):
        if not file_path:
            return self._unavailable("No file path")
        real = self.media.validate_path(file_path)
        if not real and self.config.get("external_media_mount"):
            cand = os.path.realpath(file_path)
            mount = os.path.realpath(self.config["external_media_mount"])
            if _path_inside(cand, mount) and os.path.exists(cand):
                real = cand
        if not real:
            return self._unavailable("File not found or outside allowed folders")
        return self._classify_and_mode(real, descriptor, title or os.path.basename(real),
                                       key=self.cache.local_key(real))

    def _resolve_divebar(self, descriptor):
        fid = descriptor.get("file_id")
        if not fid:
            return self._unavailable("No divebar file id")
        url = divebar.get_download_url(fid, self.config)
        if not url:
            return self._unavailable("Divebar file is not available to stream")
        ext = utils.divebar_ext(url, descriptor.get("format"))
        blob = self.cache.gcsblob_path(fid, f"blob{ext}")
        if not os.path.exists(blob):
            try:
                _download_to(url, blob)
            except Exception:
                # Don't surface the exception text — a signed mirror URL can appear in it.
                logger.exception("divebar preview fetch failed for file_id=%s", fid)
                return self._unavailable("Could not fetch this version from the mirror")
            self.cache.evict_if_needed()
        self.cache.touch(os.path.dirname(blob))
        title = descriptor.get("title") or os.path.basename(blob)
        return self._classify_and_mode(blob, descriptor, title, key=self.cache.gcs_key(fid))

    # ---- shared classification (trusted path) ---------------------------
    def _classify_and_mode(self, real, descriptor, title, key):
        res = self._classify_and_mode_inner(real, descriptor, title, key)
        # Always surface the file type + extension for the modal header.
        res.setdefault("format", media_type_label(real))
        res.setdefault("ext", os.path.splitext(real)[1].lower())
        return res

    def _classify_and_mode_inner(self, real, descriptor, title, key):
        kind = classify_kind(real)
        if kind == "audio":
            return self._mk(title, "native_audio", path=real, mime=_mime_for(real))
        if kind == "cdg_zip":
            return self._resolve_cdg(real, key, title)
        if kind == "cdg_bare":
            # Graphics-only .cdg: previewable only with a same-stem sibling audio.
            audio_candidate = sibling_cdg_audio(real)
            if not audio_candidate:
                return self._unavailable("Graphics-only .cdg — no audio track")
            # Validate the sibling against the same trust boundaries — never serve a
            # path (e.g. a symlink) that escapes the allowed media roots.
            audio = self.media.validate_path(audio_candidate)
            if not audio and self.config.get("external_media_mount"):
                cand = os.path.realpath(audio_candidate)
                mount = os.path.realpath(self.config["external_media_mount"])
                if _path_inside(cand, mount) and os.path.exists(cand):
                    audio = cand
            if not audio:
                return self._unavailable("Graphics-only .cdg — sibling audio is outside allowed folders")
            return self._mk(title, "cdg", audio=audio, graphics=real)
        if kind == "video":
            ext = os.path.splitext(real)[1].lower()
            vco, aco = _ffprobe_codecs(real)
            native = (not descriptor.get("prefer_transcode")
                      and ext in _NATIVE_VIDEO_EXTS
                      and vco in _NATIVE_VCODECS
                      and aco in _NATIVE_ACODECS)
            if native:
                return self._mk(title, "native_video", path=real, mime=_mime_for(real))
            return self._resolve_hls(real, key, title)
        return self._unavailable("Unsupported file type for preview")

    def _resolve_cdg(self, zip_path, key, title):
        cdir = self.cache.cdg_dir(key)
        audio_dst = os.path.join(cdir, "audio.mp3")
        graphics_dst = os.path.join(cdir, "graphics.cdg")
        if not (os.path.exists(audio_dst) and os.path.exists(graphics_dst)):
            os.makedirs(cdir, exist_ok=True)
            zp = ZipPlayback(self.config)
            try:
                mp3 = zp.extract_and_get_mp3(zip_path)
                cdg = zp.current_cdg_path() if mp3 else None
                if not mp3 or not cdg or not os.path.exists(cdg):
                    return self._unavailable("CDG zip is missing its .cdg/.mp3 contents")
                shutil.copyfile(mp3, audio_dst)
                shutil.copyfile(cdg, graphics_dst)
            finally:
                try:
                    zp.cleanup()
                except Exception:
                    pass
            self.cache.evict_if_needed()
        self.cache.touch(cdir)
        return self._mk(title, "cdg", audio=audio_dst, graphics=graphics_dst)

    def _resolve_hls(self, source_path, key, title):
        dest = self.cache.transcode_dir(key)
        if self.cache.is_done(key):
            self.cache.touch_transcode(key)   # refresh LRU (orders by .done mtime)
        else:
            self.transcoder.ensure_hls(source_path, dest,
                                       mark_done=lambda: self.cache.mark_done(key))
            self.cache.evict_if_needed()
        return self._mk(title, "hls", hls_dir=dest)

    # ---- token bookkeeping ----------------------------------------------
    def _mk(self, title, mode, **entry):
        token = uuid.uuid4().hex
        entry.update({"kind": mode, "created": time.time()})
        self._tokens[token] = entry
        return {"mode": mode, "token": token, "title": title or ""}

    def _unavailable(self, reason):
        return {"mode": "unavailable", "reason": reason, "title": ""}

    def _entry_for_token(self, token):
        """Return a live token entry, expiring it if it's past the TTL."""
        e = self._tokens.get(token)
        if not e:
            return None
        if time.time() - e.get("created", 0) > _TOKEN_TTL_S:
            self._tokens.pop(token, None)
            return None
        return e

    def token_info(self, token):
        return self._entry_for_token(token)

    def cdg_part_path(self, token, part):
        e = self._entry_for_token(token)
        if not e or e.get("kind") != "cdg":
            return None
        if part == "audio":
            return e.get("audio")
        if part == "graphics":
            return e.get("graphics")
        return None

    def hls_path(self, token, name):
        e = self._entry_for_token(token)
        if not e or e.get("kind") != "hls":
            return None
        if name != "index.m3u8" and not _is_seg(name):
            return None
        return os.path.join(e["hls_dir"], name)

    def close(self, token=None):
        if token is None:
            # Full teardown: drop all tokens and stop any in-flight transcode.
            self._tokens.clear()
            self.transcoder.kill_active()
            return
        # Per-token close just forgets the token. We deliberately do NOT kill the
        # transcoder here: the active job may belong to a different (e.g. pre-warming)
        # preview, and letting it finish populates the cache. A new preview bumps it.
        self._tokens.pop(token, None)

    def _gc(self):
        now = time.time()
        for t, e in list(self._tokens.items()):
            if now - e.get("created", now) > _TOKEN_TTL_S:
                self._tokens.pop(t, None)
