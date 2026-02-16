"""ZipPlayback: Extract CDG+MP3 ZIPs for VLC playback."""

import os
import shutil
import stat
import tempfile
import zipfile


class ZipPlayback:
    """Handles extraction of CDG+MP3 ZIP files for VLC playback."""

    def __init__(self, config):
        self.config = config
        self._temp_dir = None

    def extract_and_get_mp3(self, zip_path):
        """Extract a ZIP file and return the path to the .mp3 file inside.

        VLC plays the MP3 and auto-discovers the matching .cdg in the same
        directory for lyrics/graphics overlay.
        Returns the .mp3 path, or None if no .mp3 found.
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

                # Make temp dir and files world-readable (VLC runs as dietpi user)
                os.chmod(self._temp_dir, stat.S_IRWXU | stat.S_IROTH | stat.S_IXOTH | stat.S_IRGRP | stat.S_IXGRP)
                for root, dirs, files in os.walk(self._temp_dir):
                    for d in dirs:
                        os.chmod(os.path.join(root, d), stat.S_IRWXU | stat.S_IROTH | stat.S_IXOTH | stat.S_IRGRP | stat.S_IXGRP)
                    for f in files:
                        os.chmod(os.path.join(root, f), stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH | stat.S_IRGRP)

                # Find .mp3 file (VLC opens this; auto-discovers matching .cdg)
                for root, _dirs, files in os.walk(self._temp_dir):
                    for fname in files:
                        if fname.lower().endswith('.mp3'):
                            return os.path.join(root, fname)

        except (zipfile.BadZipFile, OSError):
            self.cleanup()
            return None

        # No .mp3 found
        self.cleanup()
        return None

    def cleanup(self):
        """Remove the current temporary extraction directory."""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
