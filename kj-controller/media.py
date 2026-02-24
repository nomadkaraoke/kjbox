"""MediaIndex class: scan, validate, download, and manage media files."""

import json
import os
import tempfile

from config import MEDIA_EXTENSIONS
from utils import log_message, sanitize_filename_part, parse_youtube_filename


class MediaIndex:
    """Manages the media file index (scan, persist, validate, delete, download)."""

    def __init__(self, config):
        self.config = config
        self.index = {}

    def scan(self):
        """Walk all configured media_folders, build index, persist to disk."""
        new_index = {}
        download_folder = os.path.realpath(self.config.get('download_folder', ''))
        existing = self._load_file()

        for folder in self.config.get('media_folders', []):
            folder = os.path.realpath(folder)
            if not os.path.isdir(folder):
                log_message(f"Media folder not found, skipping: {folder}", self.config)
                continue
            for dirpath, _dirnames, filenames in os.walk(folder):
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
        """Verify path resolves to within a configured media_folders entry. Returns real path or None."""
        real = os.path.realpath(filepath)
        for folder in self.config.get('media_folders', []):
            real_folder = os.path.realpath(folder)
            if real.startswith(real_folder + os.sep) or real == real_folder:
                if os.path.exists(real):
                    return real
        return None

    def is_in_download_folder(self, filepath):
        """Check if file is in the download folder (eligible for deletion)."""
        real = os.path.realpath(filepath)
        download_folder = os.path.realpath(self.config.get('download_folder', ''))
        return real.startswith(download_folder + os.sep)

    def list_items(self):
        """Return media index as a list of dicts with display info, sorted by mtime desc."""
        items = []
        for path, entry in self.index.items():
            folder = entry.get('folder', '')
            folder_name = os.path.basename(folder) if folder else 'Unknown'
            item = {
                "file_path": entry["path"],
                "display_name": entry.get("display_name", entry.get("filename", "")),
                "filename": entry.get("filename", ""),
                "folder_name": folder_name,
                "folder": folder,
                "is_download": entry.get("is_download", False),
                "mtime": entry.get("mtime", 0),
                "size": entry.get("size", 0),
            }
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
        cookies_file = self.config.get('youtube_cookies_file', '')

        # Phase 1: Extract metadata without downloading
        extract_opts = {
            'quiet': True,
            'noplaylist': True,
        }
        if cookies_file and os.path.exists(cookies_file):
            extract_opts['cookiefile'] = cookies_file

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

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'force_overwrites': True,
            'quiet': True,
            'noplaylist': True,
            'writethumbnail': True,
        }
        if cookies_file and os.path.exists(cookies_file):
            ydl_opts['cookiefile'] = cookies_file
            log_message(f"Using YouTube cookies file: {cookies_file}", self.config)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(youtube_url, download=True)

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

            self.index[real_path] = entry
            self.save()

            log_message(f"Successfully downloaded '{title}' as {os.path.basename(real_path)}", self.config)
            return real_path, title
        except Exception as e:
            log_message(f"Error downloading video: {e}", self.config)
            return None, None
