"""ZipPlayback: Extract CDG+MP3 ZIPs for VLC playback."""

import os
import shutil
import tempfile
import zipfile


class ZipPlayback:
    """Handles extraction of CDG+MP3 ZIP files for VLC playback."""

    def __init__(self, config):
        self.config = config
        self._temp_dir = None

    def extract_and_get_cdg(self, zip_path):
        """Extract a ZIP file and return the path to the .cdg file inside.

        VLC auto-discovers the matching .mp3 in the same directory.
        Returns the .cdg path, or None if no .cdg found.
        """
        self.cleanup()

        if not os.path.isfile(zip_path):
            return None

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Validate: no path traversal
                for member in zf.namelist():
                    if '..' in member or os.path.isabs(member):
                        return None

                self._temp_dir = tempfile.mkdtemp(prefix='kj-zip-extract-')
                zf.extractall(self._temp_dir)

                # Find .cdg file
                for root, _dirs, files in os.walk(self._temp_dir):
                    for fname in files:
                        if fname.lower().endswith('.cdg'):
                            return os.path.join(root, fname)

        except (zipfile.BadZipFile, OSError):
            self.cleanup()
            return None

        # No .cdg found
        self.cleanup()
        return None

    def cleanup(self):
        """Remove the current temporary extraction directory."""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
