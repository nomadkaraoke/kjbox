"""ZipPlayback: Extract CDG+MP3 ZIPs for VLC playback."""

import logging
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile

logger = logging.getLogger(__name__)


class ZipPlayback:
    """Handles extraction of CDG+MP3 ZIP files for VLC playback."""

    def __init__(self, config):
        self.config = config
        self._temp_dir = None
        self._mp3_path = None

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
                try:
                    zf.extractall(self._temp_dir)
                except NotImplementedError:
                    # Python's zipfile only handles STORED/DEFLATE. Legacy karaoke
                    # discs (e.g. "MP3+G Toolz .NET") use Deflate64 (method 9),
                    # which raises NotImplementedError. Fall back to system unzip,
                    # which supports it. namelist() above already rejected any
                    # path-traversal entries, so this extraction is safe.
                    if not self._extract_with_unzip(zip_path, self._temp_dir):
                        self.cleanup()
                        return None

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
                            self._mp3_path = os.path.join(root, fname)
                            return self._mp3_path

        except (zipfile.BadZipFile, OSError):
            self.cleanup()
            return None

        # No .mp3 found
        self.cleanup()
        return None

    def current_cdg_path(self):
        """Return the .cdg matching the extracted .mp3, or None.

        Valid only after a successful ``extract_and_get_mp3`` (before
        ``cleanup``). mpv must be handed the .cdg directly to render the CDG
        graphics (with the .mp3 supplied as an external audio track); VLC
        instead auto-discovers the sibling .cdg from the .mp3.

        The result is tied to the specific .mp3 ``extract_and_get_mp3`` chose:
        a zip could (rarely) hold more than one CDG/MP3 pair, so we return the
        .cdg that lives beside that .mp3 and shares its stem, never just the
        first .cdg anywhere in the temp tree.
        """
        if not self._mp3_path or not os.path.isfile(self._mp3_path):
            return None
        mp3_dir = os.path.dirname(self._mp3_path)
        mp3_stem = os.path.splitext(os.path.basename(self._mp3_path))[0].lower()
        try:
            cdgs = [f for f in os.listdir(mp3_dir) if f.lower().endswith('.cdg')]
        except OSError:
            return None
        # Prefer the .cdg sharing the .mp3's stem (the normal X.mp3 / X.cdg pair).
        for fname in cdgs:
            if os.path.splitext(fname)[0].lower() == mp3_stem:
                return os.path.join(mp3_dir, fname)
        # Otherwise fall back to a .cdg sitting beside the chosen .mp3.
        if cdgs:
            return os.path.join(mp3_dir, sorted(cdgs)[0])
        return None

    def _extract_with_unzip(self, zip_path, dest_dir):
        """Extract a ZIP using the system `unzip`, for compression methods
        Python's zipfile can't handle (e.g. Deflate64). Returns True on success.

        Caller must have already validated entry paths (no traversal). `unzip`
        also refuses absolute/`..` paths by default as defence in depth.
        """
        try:
            result = subprocess.run(
                ['unzip', '-o', '-qq', zip_path, '-d', dest_dir],
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("unzip fallback failed for %s: %s", zip_path, exc)
            return False
        if result.returncode != 0:
            logger.error(
                "unzip fallback returned %s for %s: %s",
                result.returncode, zip_path,
                result.stderr.decode('utf-8', 'replace').strip(),
            )
            return False
        return True

    def cleanup(self):
        """Remove the current temporary extraction directory."""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        self._mp3_path = None
