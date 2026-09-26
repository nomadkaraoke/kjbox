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
# Both incidents observed so far failed fast (immediate SCSI "Not Ready"), but
# a future failure mode of the bridge could block a filesystem call instead of
# erroring — run the probe on its own thread with this deadline so a stuck
# syscall can't stall the poll loop and silently stop producing alerts.
PROBE_TIMEOUT_SECONDS = 3


class ExternalMediaMonitor:
    """Background poller that alerts when `external_media_mount` stops
    responding to plain filesystem reads."""

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._alert = None
        self._thread = None
        self._stop_event = threading.Event()
        self._probe_thread = None
        self.probe_timeout = PROBE_TIMEOUT_SECONDS
        # Generous bound for start() to wait out an in-flight stop() before
        # spawning a replacement thread — covers the current check_once()
        # call finishing (bounded by probe_timeout) plus its own processing.
        self.stop_join_timeout = CHECK_INTERVAL_SECONDS + PROBE_TIMEOUT_SECONDS + 1

    def start(self):
        """Start the background poll loop. No-op if no mount is configured,
        or if a previously-started loop is still running and healthy."""
        if not self.config.get('external_media_mount', ''):
            return
        if self._thread is not None and self._thread.is_alive():
            if self._stop_event.is_set():
                # A stop() is already in flight — wait for the old loop to
                # actually exit before spawning its replacement, so a
                # stop()-then-start() pair can't leave monitoring silently off.
                self._thread.join(self.stop_join_timeout)
            if self._thread.is_alive():
                return  # still shutting down — caller can retry start() later
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='ssd-health')
        self._thread.start()

    def stop(self):
        """Signal the poll loop to exit. Wakes it immediately rather than
        waiting out the current CHECK_INTERVAL_SECONDS sleep."""
        self._stop_event.set()

    @property
    def alert(self):
        """Current alert dict ({mount, message, since}), or None if healthy."""
        with self._lock:
            return dict(self._alert) if self._alert else None

    def _loop(self):
        while not self._stop_event.is_set():
            self.check_once()
            self._stop_event.wait(CHECK_INTERVAL_SECONDS)

    def check_once(self):
        """Run a single probe and update alert state. Public for testability."""
        mount = self.config.get('external_media_mount', '')
        if not mount:
            return
        healthy = self._probe_with_timeout(mount)
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

    def _probe_with_timeout(self, mount):
        """Run _probe() on its own thread with a deadline.

        os.path.ismount()/os.listdir() are ordinary blocking syscalls with no
        built-in timeout, and nothing in this thread can forcibly interrupt a
        stuck one — so isolate the call on a daemon thread and just stop
        waiting on it. A probe that's still running after the deadline is
        treated as unhealthy; the orphaned thread is harmless in isolation,
        but a prolonged failure polls every CHECK_INTERVAL_SECONDS, so at most
        one probe thread is ever kept in flight at a time — a still-running
        one from a prior cycle short-circuits this call as unhealthy instead
        of piling on another thread every poll.
        """
        if self._probe_thread is not None and self._probe_thread.is_alive():
            return False

        result = {}

        def _run():
            result['ok'] = self._probe(mount)

        t = threading.Thread(target=_run, daemon=True)
        self._probe_thread = t
        t.start()
        t.join(self.probe_timeout)
        if t.is_alive():
            return False
        return result.get('ok', False)

    def probe_now(self):
        """One-off bounded health probe for request handlers — True if healthy.

        Used when a library read has just failed, to tell "drive dropped out"
        from "bad file" without waiting up to FAILURE_THRESHOLD poll cycles for
        the alert. Runs on its own thread (never the poll loop's
        ``_probe_thread``, so it can't make a concurrent poll read as failed).
        """
        mount = self.config.get('external_media_mount', '')
        if not mount:
            return True
        result = {}

        def _run():
            result['ok'] = self._probe(mount)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(self.probe_timeout)
        return not t.is_alive() and result.get('ok', False)

    @staticmethod
    def _probe(mount):
        """Cheap, fast health check. Returns True if the mount looks healthy.

        Needs both checks: a wedged bridge stays registered as mounted (df -h
        still showed it mounted during the real incidents) while every read
        fails, so os.listdir() alone catches that. But a *cleanly* unmounted
        drive reverts the mount point to an ordinary (often empty, readable)
        directory on the underlying filesystem — os.listdir() would happily
        succeed there and falsely report healthy, so os.path.ismount() is
        checked first to catch that case too.
        """
        try:
            if not os.path.ismount(mount):
                return False
            os.listdir(mount)
            return True
        except OSError:
            return False
