"""ExternalMediaMonitor: detects when the external media drive (the 4TB USB
SSD on NomadPC) has dropped offline, so the KJ gets an unmissable banner
instead of silently hitting 400 "Invalid or inaccessible file path" on /play.

Deliberately does NOT use smartctl or any NVMe admin passthrough — that is
what hung the SanDisk Extreme Pro's internal ASMedia bridge firmware in the
first place (see docs/TROUBLESHOOTING.md, "4TB USB SSD Drops Offline"). This
only does a plain os.listdir() on the mount root, which fails fast with an
immediate OSError when the bridge is wedged rather than blocking, so it's
safe to poll frequently.
"""

import os
import threading
import time

from utils import log_message

CHECK_INTERVAL_SECONDS = 5
# Require this many consecutive failures before alerting, so a single
# transient hiccup can't flap the banner on and off.
FAILURE_THRESHOLD = 2


class ExternalMediaMonitor:
    """Background poller that alerts when `external_media_mount` stops
    responding to plain filesystem reads."""

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._alert = None
        self._thread = None
        self._stop = False

    def start(self):
        """Start the background poll loop. No-op if no mount is configured."""
        if not self.config.get('external_media_mount', ''):
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name='ssd-health')
        self._thread.start()

    def stop(self):
        self._stop = True

    @property
    def alert(self):
        """Current alert dict ({mount, message, since}), or None if healthy."""
        with self._lock:
            return dict(self._alert) if self._alert else None

    def _loop(self):
        while not self._stop:
            self.check_once()
            time.sleep(CHECK_INTERVAL_SECONDS)

    def check_once(self):
        """Run a single probe and update alert state. Public for testability."""
        mount = self.config.get('external_media_mount', '')
        if not mount:
            return
        healthy = self._probe(mount)
        with self._lock:
            if healthy:
                if self._consecutive_failures or self._alert:
                    log_message(f"External media mount recovered: {mount}", self.config)
                self._consecutive_failures = 0
                self._alert = None
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= FAILURE_THRESHOLD and not self._alert:
                label = os.path.basename(mount.rstrip('/')) or mount
                self._alert = {
                    'mount': mount,
                    'message': (
                        f"SSD disconnected ({label}) — unplug and replug it now!"
                    ),
                    'since': time.time(),
                }
                log_message(
                    f"External media mount unresponsive: {mount} — "
                    "SSD likely needs a physical unplug/replug.",
                    self.config,
                )

    @staticmethod
    def _probe(mount):
        """Cheap, fast health check. Returns True if the mount looks healthy.

        Deliberately doesn't check os.path.ismount() first: during the real
        incident the mount stayed registered (df -h still showed it mounted)
        while every read failed, so ismount() alone would miss it. A plain
        os.listdir() catches both a wedged bridge (OSError: I/O error) and an
        actually-vanished mountpoint (OSError: no such file).
        """
        try:
            os.listdir(mount)
            return True
        except OSError:
            return False
