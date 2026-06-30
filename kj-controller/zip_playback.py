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
        extract_dir = self._extract_to_temp(zip_path)
        if extract_dir is None:
            return None
        # Find .mp3 file (VLC opens this; auto-discovers matching .cdg)
        for root, _dirs, files in os.walk(extract_dir):
            for fname in files:
                if fname.lower().endswith('.mp3'):
                    self._mp3_path = os.path.join(root, fname)
                    return self._mp3_path
        # No .mp3 found
        self.cleanup()
        return None

    def extract(self, zip_path):
        """Extract a ZIP into a temp dir and return that dir (or None).

        Unlike ``extract_and_get_mp3`` this does not look for a specific audio
        file — the playability checker scans the returned dir for the .cdg and
        an audio track of any supported format. The temp dir persists until
        ``cleanup()`` is called.
        """
        return self._extract_to_temp(zip_path)

    def _extract_to_temp(self, zip_path):
        """Extract the whole zip into a fresh temp dir; return the dir or None.

        Robust against (a) Deflate64 members (Python can't inflate → system
        ``unzip``) and (b) a single corrupt member raising ``zlib.error``
        mid-extract — Python's ``extractall`` aborts the entire zip, but system
        ``unzip`` skips only the bad member and still extracts the good
        .cdg/.mp3. A genuinely-invalid zip (unreadable central directory)
        returns None so the caller can report "not a valid zip".
        """
        import zlib

        self.cleanup()
        if not os.path.isfile(zip_path):
            return None

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                self._temp_dir = tempfile.mkdtemp(prefix='kj-zip-extract-')
                # Reject only genuine path traversal — a member that resolves
                # OUTSIDE the temp dir. A naive `'..' in name` check wrongly
                # rejects legitimate filenames like "S.O.S..cdg" (a double dot
                # before the extension), which are common karaoke titles.
                dest_real = os.path.realpath(self._temp_dir)
                for member in zf.namelist():
                    target = os.path.realpath(os.path.join(self._temp_dir, member))
                    if target != dest_real and not target.startswith(dest_real + os.sep):
                        self.cleanup()
                        return None
                try:
                    zf.extractall(self._temp_dir)
                except (NotImplementedError, zlib.error, zipfile.BadZipFile,
                        OSError, EOFError) as exc:
                    # Deflate64 (NotImplementedError) or a corrupt member
                    # (zlib.error/EOFError). namelist() above already rejected
                    # path-traversal entries, so the unzip fallback is safe.
                    logger.warning(
                        "zipfile.extractall failed for %s (%s); trying system unzip",
                        zip_path, exc,
                    )
                    if not self._extract_with_unzip(zip_path, self._temp_dir):
                        self.cleanup()
                        return None
        except (zipfile.BadZipFile, OSError):
            self.cleanup()
            return None

        self._chmod_tree(self._temp_dir)
        return self._temp_dir

    def _chmod_tree(self, root):
        """Make the extracted tree world-readable (players may run as another
        user, e.g. VLC as ``dietpi``)."""
        os.chmod(root, stat.S_IRWXU | stat.S_IROTH | stat.S_IXOTH | stat.S_IRGRP | stat.S_IXGRP)
        for dirpath, dirs, files in os.walk(root):
            for d in dirs:
                os.chmod(os.path.join(dirpath, d), stat.S_IRWXU | stat.S_IROTH | stat.S_IXOTH | stat.S_IRGRP | stat.S_IXGRP)
            for f in files:
                os.chmod(os.path.join(dirpath, f), stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH | stat.S_IRGRP)

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
            # unzip returns non-zero (typically 1/2) when it skips a corrupt
            # member, but it still extracts every good one. Treat the run as a
            # success as long as something landed on disk; only a truly empty
            # extraction is a real failure.
            logger.warning(
                "unzip returned %s for %s: %s",
                result.returncode, zip_path,
                result.stderr.decode('utf-8', 'replace').strip(),
            )
        for _root, _dirs, files in os.walk(dest_dir):
            if files:
                return True
        return False

    def cleanup(self):
        """Remove the current temporary extraction directory."""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        self._mp3_path = None
