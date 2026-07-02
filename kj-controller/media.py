"""MediaIndex class: scan, validate, download, and manage media files."""

import json
import os
import shutil
import tempfile
import unicodedata
import zipfile

import requests

from config import MEDIA_EXTENSIONS, resolve_preview_cache_dir
from utils import log_message, sanitize_filename_part, parse_youtube_filename
from naming import (
    parse_identity, extract_media_id, media_id_for, content_hash,
    SOURCE_UPLOAD,
)

# Rejected downloads are moved here (a subdir of the download folder) instead of
# being deleted. scan() skips this dir so quarantined files are never re-indexed.
QUARANTINE_DIRNAME = "_playability_quarantine"


def _gate_playable(path, config):
    """Fast inline playability check (integrity + sampled decode, no render).
    Returns PlayabilityResult; verdict['overall_ok'] is False for bad files."""
    from playability import PlayabilityChecker
    return PlayabilityChecker(config=config).check(path, renderers=(), depth="quick")


def _quarantine_download(file_path, reason, config):
    """Move a rejected download (and its sidecars) into a quarantine subdir
    instead of deleting it.

    An automated playability verdict can be wrong, so it must never irreversibly
    destroy a file. The quarantine dir lives beside the download and is skipped
    by scan(), so quarantined files are never re-indexed as playable. Returns
    the quarantined path of the main file, or None if nothing was moved.
    """
    parent = os.path.dirname(file_path)
    qdir = os.path.join(parent, QUARANTINE_DIRNAME)
    base_no_ext = os.path.splitext(file_path)[0]
    try:
        os.makedirs(qdir, exist_ok=True)
    except OSError as exc:
        log_message(f"Quarantine dir create failed for {file_path}: {exc}", config)
        return None
    moved_main = None
    try:
        siblings = os.listdir(parent)
    except OSError:
        siblings = [os.path.basename(file_path)]
    for fname in siblings:
        full = os.path.join(parent, fname)
        # Match the rejected file and its yt-dlp sidecars (same stem, e.g. a
        # .webp/.jpg thumbnail) — same matching the old delete path used.
        if not os.path.isfile(full) or os.path.splitext(full)[0] != base_no_ext:
            continue
        try:
            dest = os.path.join(qdir, fname)
            shutil.move(full, dest)
            if os.path.abspath(full) == os.path.abspath(file_path):
                moved_main = dest
        except OSError as exc:
            log_message(f"Quarantine move failed for {full}: {exc}", config)
    if moved_main:
        try:
            with open(moved_main + ".reason.txt", "w", encoding="utf-8") as fh:
                fh.write((reason or "not playable") + "\n")
        except OSError:
            pass
    return moved_main


def _ytdlp_base_opts(config):
    """Common yt-dlp options with anti-detection and cookie support."""
    opts = {
        'quiet': True,
        'noplaylist': True,
        'retries': 3,
        'fragment_retries': 3,
        'extractor_retries': 3,
        'sleep_interval': 1,
        'max_sleep_interval': 5,
        'sleep_interval_requests': 1,
    }
    cookies_file = config.get('youtube_cookies_file', '')
    if cookies_file and os.path.exists(cookies_file):
        opts['cookiefile'] = cookies_file
    return opts


class MediaIndex:
    """Manages the media file index (scan, persist, validate, delete, download)."""

    def __init__(self, config, media_library=None):
        self.config = config
        self.index = {}
        self.media_library = media_library

    def _resolve_media_id(self, real_path, fname):
        """Return (media_id, identity_dict) for a file.

        Prefers an embedded [media_id] slug token (post-migration, cheap); else
        derives from the filename pattern; for keyless uploads reuses an existing
        media_library row (by path) to avoid re-hashing on every scan.
        """
        identity = parse_identity(fname)
        token = extract_media_id(fname)
        if token:
            return token, identity
        if identity["source_ref"]:
            return media_id_for(identity["source"], identity["source_ref"]), identity
        # keyless upload: reuse a prior row's id if we already indexed this path
        if self.media_library is not None:
            existing = self.media_library.get_by_path(real_path)
            if existing:
                return existing["media_id"], identity
        return media_id_for(SOURCE_UPLOAD, content_hash(real_path)), identity

    def scan(self):
        """Walk all configured media_folders, build index, persist to disk."""
        new_index = {}
        download_folder = os.path.realpath(self.config.get('download_folder', ''))
        # The browser-preview cache normally lives outside every indexed path, but
        # prune it defensively in case it's ever configured inside one — otherwise
        # its transcode/CDG artifacts get indexed as phantom "graphics"/"audio"
        # download rows.
        preview_cache_dir = os.path.realpath(resolve_preview_cache_dir(self.config))
        existing = self._load_file()

        for folder in self.config.get('media_folders', []):
            folder = os.path.realpath(folder)
            if not os.path.isdir(folder):
                log_message(f"Media folder not found, skipping: {folder}", self.config)
                continue
            for dirpath, dirnames, filenames in os.walk(folder):
                # Never index quarantined (rejected) downloads or preview-cache artifacts.
                dirnames[:] = [
                    d for d in dirnames
                    if d != QUARANTINE_DIRNAME
                    and os.path.realpath(os.path.join(dirpath, d)) != preview_cache_dir
                ]
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in MEDIA_EXTENSIONS:
                        continue
                    full_path = os.path.join(dirpath, fname)
                    real_path = os.path.realpath(full_path)
                    try:
                        stat = os.stat(real_path)
                    except OSError:
                        continue

                    is_download = real_path.startswith(download_folder + os.sep) or real_path == download_folder
                    parsed = parse_youtube_filename(fname)

                    entry = {
                        "path": real_path,
                        "filename": fname,
                        "folder": folder,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "is_download": is_download,
                    }

                    if parsed:
                        entry["youtube_id"] = parsed[0]
                        entry["channel"] = parsed[1]
                        entry["title"] = parsed[2]
                        entry["display_name"] = parsed[2]
                    else:
                        entry["display_name"] = os.path.splitext(fname)[0]

                    if self.media_library is not None:
                        try:
                            media_id, identity = self._resolve_media_id(real_path, fname)
                            entry["media_id"] = media_id
                            self.media_library.upsert({
                                "media_id": media_id,
                                "source": identity["source"],
                                "source_ref": identity["source_ref"],
                                "artist": identity["artist"],
                                "title": identity["title"],
                                "confidence": identity["confidence"],
                                "parse_method": identity["parse_method"],
                                "needs_review": identity["needs_review"],
                                "raw_original_name": fname,
                                "file_path": real_path,
                                "ext": ext,
                            })
                        except Exception as exc:  # never let indexing crash on one file
                            log_message(f"media_library upsert failed for {fname}: {exc}", self.config)

                    # Preserve duration and upload_date from existing index
                    if real_path in existing:
                        for key in ('duration', 'upload_date', 'original_url'):
                            if key in existing[real_path]:
                                entry[key] = existing[real_path][key]

                    new_index[real_path] = entry

        self.index = new_index
        self.save()
        log_message(f"Media scan complete: {len(self.index)} files indexed.", self.config)
        return self.index

    def save(self):
        """Persist index to disk.

        Uses atomic write (temp file + fsync + rename) so power loss mid-write
        cannot corrupt the index file.
        """
        index_path = self.config.get('media_index_path', 'media_index.json')
        try:
            dir_name = os.path.dirname(os.path.abspath(index_path))
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.json')
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(self.index, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, index_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            log_message(f"Error saving media index: {e}", self.config)

    def _load_file(self):
        """Read media_index.json from disk, return dict."""
        index_path = self.config.get('media_index_path', 'media_index.json')
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                log_message(f"Error reading media index file: {e}", self.config)
        return {}

    def load(self):
        """Load index into memory; if no file exists, do initial full scan."""
        loaded = self._load_file()
        if loaded:
            self.index = loaded
            log_message(f"Loaded media index with {len(self.index)} entries.", self.config)
        else:
            log_message("No media index found, performing initial scan...", self.config)
            self.scan()

    def validate_path(self, filepath):
        """Verify path resolves to within a configured media_folders entry. Returns real path or None.

        Tries both NFC and NFD Unicode normalization forms to handle filenames
        with diacritics (e.g. ç, à) that may change form during JSON round-trips
        through the browser.
        """
        for form in ('NFC', 'NFD'):
            candidate = os.path.realpath(unicodedata.normalize(form, filepath))
            for folder in self.config.get('media_folders', []):
                real_folder = os.path.realpath(folder)
                if candidate.startswith(real_folder + os.sep) or candidate == real_folder:
                    if os.path.exists(candidate):
                        return candidate
        return None

    def is_in_download_folder(self, filepath):
        """Check if file is in the download folder (eligible for deletion)."""
        real = os.path.realpath(filepath)
        download_folder = os.path.realpath(self.config.get('download_folder', ''))
        return real.startswith(download_folder + os.sep)

    def list_items(self):
        """Return media index as a list of dicts with display info, sorted by mtime desc."""
        from utils import media_type_label
        from playability import sibling_cdg_audio
        items = []
        for path, entry in self.index.items():
            folder = entry.get('folder', '')
            folder_name = os.path.basename(folder) if folder else 'Unknown'
            ext = os.path.splitext(entry.get("filename", ""))[1].lower()
            item = {
                "file_path": entry["path"],
                "display_name": entry.get("display_name", entry.get("filename", "")),
                "filename": entry.get("filename", ""),
                "folder_name": folder_name,
                "folder": folder,
                "ext": ext,
                "media_kind": media_type_label(ext),
                "is_download": entry.get("is_download", False),
                "mtime": entry.get("mtime", 0),
                "size": entry.get("size", 0),
            }
            # A bare .cdg is graphics-only unless a same-stem audio file sits beside it.
            if ext == ".cdg":
                item["cdg_no_audio"] = sibling_cdg_audio(entry["path"]) is None
            if "channel" in entry:
                item["channel"] = entry["channel"]
            if "youtube_id" in entry:
                item["youtube_id"] = entry["youtube_id"]
            if "duration" in entry:
                item["duration"] = entry["duration"]
            items.append(item)

        items.sort(key=lambda x: x['mtime'], reverse=True)
        return items

    def delete_file(self, validated_path):
        """Delete a media file plus sidecar files and remove from index."""
        # Delete the main file
        os.remove(validated_path)
        log_message(f"Deleted file: {os.path.basename(validated_path)}", self.config)

        # Delete same-basename sidecar files (.json, .webp, .jpg, etc.)
        basename_no_ext = os.path.splitext(validated_path)[0]
        parent_dir = os.path.dirname(validated_path)
        for fname in os.listdir(parent_dir):
            full = os.path.join(parent_dir, fname)
            if full != validated_path and os.path.splitext(full)[0] == basename_no_ext:
                try:
                    os.remove(full)
                    log_message(f"Deleted sidecar: {fname}", self.config)
                except Exception as e:
                    log_message(f"Error deleting sidecar {fname}: {e}", self.config)

        # Remove from index
        if validated_path in self.index:
            del self.index[validated_path]
            self.save()

    def download_video(self, youtube_url):
        """Downloads a YouTube video with descriptive filename, updates media index."""
        import yt_dlp

        download_folder = self.config.get('download_folder', os.path.expanduser("~/kjdata/videos"))
        os.makedirs(download_folder, exist_ok=True)

        # Phase 1: Extract metadata without downloading
        extract_opts = _ytdlp_base_opts(self.config)

        try:
            with yt_dlp.YoutubeDL(extract_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
                title = info.get('title', 'Unknown Title')
                channel = info.get('channel', info.get('uploader', 'Unknown'))
                youtube_id = info.get('id', 'unknown')
                duration = info.get('duration')
                upload_date = info.get('upload_date')
        except Exception as e:
            log_message(f"Error extracting video info: {e}", self.config)
            return None, None

        # Phase 2: Build descriptive filename and download
        safe_channel = sanitize_filename_part(channel)
        safe_title = sanitize_filename_part(title)
        basename = f"{youtube_id}__{safe_channel}__{safe_title}"
        output_template = os.path.join(download_folder, basename)

        ydl_opts = _ytdlp_base_opts(self.config)
        ydl_opts.update({
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'force_overwrites': True,
            'writethumbnail': True,
        })
        if ydl_opts.get('cookiefile'):
            log_message(f"Using YouTube cookies file: {ydl_opts['cookiefile']}", self.config)

        try:
            self._run_ytdlp_download(ydl_opts, youtube_url)

            # Find the actual downloaded file (might be .mp4, .mkv, etc.)
            file_path = None
            for ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov']:
                candidate = output_template + ext
                if os.path.exists(candidate):
                    file_path = candidate
                    break

            if not file_path:
                log_message(f"ERROR: Downloaded file not found for {basename}", self.config)
                return None, None

            real_path = os.path.realpath(file_path)

            gate = _gate_playable(file_path, self.config)
            if not gate.verdict.get("overall_ok"):
                reason = "; ".join(gate.verdict.get("reasons") or ["not playable"])
                log_message(f"Download rejected (not playable): {os.path.basename(file_path)} — {reason}", self.config)
                # Quarantine (never delete) — an automated verdict can be wrong,
                # so move the file + yt-dlp sidecars aside for review instead of
                # irreversibly removing them. scan() skips the quarantine dir.
                qpath = _quarantine_download(file_path, reason, self.config)
                if qpath:
                    log_message(f"Quarantined to {qpath}", self.config)
                return None, None

            # Add to media index
            stat = os.stat(real_path)
            entry = {
                "path": real_path,
                "filename": os.path.basename(real_path),
                "folder": os.path.realpath(download_folder),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "is_download": True,
                "youtube_id": youtube_id,
                "channel": safe_channel,
                "title": safe_title,
                "display_name": safe_title,
                "original_url": youtube_url,
            }
            if duration is not None:
                entry["duration"] = duration
            if upload_date is not None:
                entry["upload_date"] = upload_date

            entry["playability"] = gate.verdict
            self.index[real_path] = entry
            self.save()

            log_message(f"Successfully downloaded '{title}' as {os.path.basename(real_path)}", self.config)
            return real_path, title
        except Exception as e:
            log_message(f"Error downloading video: {e}", self.config)
            return None, None

    def _run_ytdlp_download(self, ydl_opts, url):
        """Execute yt-dlp download. Extracted for testability."""
        import yt_dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

    def download_from_url(self, url, filename=None):
        """Downloads a file from a direct HTTP URL (e.g. Google Drive), updates media index."""
        download_folder = self.config.get('download_folder', os.path.expanduser("~/kjdata/videos"))
        os.makedirs(download_folder, exist_ok=True)

        try:
            if filename:
                # Filename known upfront — compute file_path before downloading so
                # _http_download receives the final destination (enables test mocking).
                safe_name = sanitize_filename_part(os.path.splitext(filename)[0])
                ext = os.path.splitext(filename)[1] or '.mp4'
                final_name = f"divebar__{safe_name}{ext}"
                file_path = os.path.join(download_folder, final_name)
                # _http_download writes the body to file_path and returns the response.
                self._http_download(url, file_path)
                display_name = os.path.splitext(filename)[0]
            else:
                # Filename unknown — fetch response first (body not consumed) to read
                # Content-Disposition, then stream body once file_path is known.
                resp = self._http_download(url, None)
                cd = resp.headers.get('Content-Disposition', '')
                if 'filename=' in cd:
                    filename = cd.split('filename=')[-1].strip('"\'')
                else:
                    filename = url.split('/')[-1].split('?')[0] or 'download'
                safe_name = sanitize_filename_part(os.path.splitext(filename)[0])
                ext = os.path.splitext(filename)[1] or '.mp4'
                final_name = f"divebar__{safe_name}{ext}"
                file_path = os.path.join(download_folder, final_name)
                display_name = os.path.splitext(filename)[0]
                with open(file_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)

            gate = _gate_playable(file_path, self.config)
            if not gate.verdict.get("overall_ok"):
                reason = "; ".join(gate.verdict.get("reasons") or ["not playable"])
                log_message(f"Download rejected (not playable): {os.path.basename(file_path)} — {reason}", self.config)
                # Quarantine (never delete) — an automated verdict can be wrong,
                # so move the file + yt-dlp sidecars aside for review instead of
                # irreversibly removing them. scan() skips the quarantine dir.
                qpath = _quarantine_download(file_path, reason, self.config)
                if qpath:
                    log_message(f"Quarantined to {qpath}", self.config)
                return None, None

            real_path = os.path.realpath(file_path)
            stat = os.stat(real_path)

            entry = {
                "path": real_path,
                "filename": os.path.basename(real_path),
                "folder": os.path.realpath(download_folder),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "is_download": True,
                "display_name": display_name,
                "original_url": url,
                "source": "divebar",
            }

            entry["playability"] = gate.verdict
            self.index[real_path] = entry
            self.save()

            log_message(f"Successfully downloaded '{display_name}' from Divebar", self.config)
            return real_path, display_name

        except Exception as e:
            log_message(f"Error downloading from URL: {e}", self.config)
            return None, None

    def download_cdg_pair(self, cdg_url, mp3_url, filename):
        """Download a loose CDG + its sibling audio and package them into a single
        cdg+mp3 ``.zip`` — the playable form the rest of the pipeline expects.

        Some brands (e.g. Sandell Karaoke) store a CDG's graphics and its audio as
        two separate Drive files rather than one zip, so the divebar index exposes
        them as independent track rows. Downloading the ``.cdg`` alone yields a
        silent, useless file; this fetches both and zips them, then runs the same
        playability gate every other download passes through.

        Returns ``(real_path, display_name)`` on success, or ``(None, None)`` when
        a download fails or the resulting zip is rejected by the gate (in which
        case the zip is quarantined rather than left clickable)."""
        download_folder = self.config.get('download_folder', os.path.expanduser("~/kjdata/videos"))
        os.makedirs(download_folder, exist_ok=True)

        base = sanitize_filename_part(os.path.splitext(filename)[0]) if filename else "cdg-download"
        display_name = os.path.splitext(filename)[0] if filename else base
        final_name = f"divebar__{base}.zip"
        file_path = os.path.join(download_folder, final_name)

        # Stage the two members in a system temp dir (NOT the download folder, so a
        # concurrent scan() can never index the loose cdg/mp3 before they're zipped).
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix="cdgpair_")
            cdg_tmp = os.path.join(tmpdir, base + ".cdg")
            mp3_tmp = os.path.join(tmpdir, base + ".mp3")
            self._http_download(cdg_url, cdg_tmp)
            self._http_download(mp3_url, mp3_tmp)

            # Store (not deflate): .cdg/.mp3 are already compact, and the zip play
            # path only needs to read the members back out.
            with zipfile.ZipFile(file_path, 'w', zipfile.ZIP_STORED) as zf:
                zf.write(cdg_tmp, arcname=os.path.basename(cdg_tmp))
                zf.write(mp3_tmp, arcname=os.path.basename(mp3_tmp))

            gate = _gate_playable(file_path, self.config)
            if not gate.verdict.get("overall_ok"):
                reason = "; ".join(gate.verdict.get("reasons") or ["not playable"])
                log_message(f"Download rejected (not playable): {os.path.basename(file_path)} — {reason}", self.config)
                qpath = _quarantine_download(file_path, reason, self.config)
                if qpath:
                    log_message(f"Quarantined to {qpath}", self.config)
                return None, None

            real_path = os.path.realpath(file_path)
            stat = os.stat(real_path)
            entry = {
                "path": real_path,
                "filename": os.path.basename(real_path),
                "folder": os.path.realpath(download_folder),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "is_download": True,
                "display_name": display_name,
                "original_url": cdg_url,
                "source": "divebar",
                "playability": gate.verdict,
            }
            self.index[real_path] = entry
            self.save()
            log_message(f"Successfully downloaded '{display_name}' (cdg+mp3 zip) from Divebar", self.config)
            return real_path, display_name

        except Exception as e:
            log_message(f"Error downloading CDG pair from URL: {e}", self.config)
            # Remove any partial/failed zip so it can't be picked up later.
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass
            return None, None
        finally:
            if tmpdir and os.path.isdir(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)

    def _http_download(self, url, file_path):
        """Execute HTTP GET and write streaming body to file_path (when provided).
        Returns the response object for header inspection by the caller.
        Extracted for testability: tests mock this method to create a fake file and
        return a MagicMock response."""
        resp = requests.get(url, stream=True, timeout=120, allow_redirects=True)
        resp.raise_for_status()
        if file_path is not None:
            with open(file_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
        return resp
