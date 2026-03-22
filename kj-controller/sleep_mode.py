"""Sleep mode manager for NomadPC power conservation."""

import json
import os
import subprocess

from config import APP_DIR
from utils import log_message

SLEEP_FLAG = '/tmp/kj-sleep-mode'
SLEEP_STATE = '/tmp/kj-sleep-state.json'
ENTER_SCRIPT = os.path.join(APP_DIR, 'sleep-enter.sh')
EXIT_SCRIPT = os.path.join(APP_DIR, 'sleep-exit.sh')


class SleepManager:
    """Orchestrates entering and exiting sleep mode.

    Sleep mode stops non-essential services, unmounts the USB SSD,
    and switches to power-saver profile. The kj-controller stays
    running so the UI can wake the system back up.
    """

    def __init__(self):
        self._entering = False
        self._exiting = False

    def is_sleeping(self):
        """Check if sleep mode is currently active."""
        return os.path.exists(SLEEP_FLAG)

    def get_status(self):
        """Return current sleep mode status with details."""
        sleeping = self.is_sleeping()
        result = {
            'active': sleeping,
            'entering': self._entering,
            'exiting': self._exiting,
        }
        if sleeping and os.path.exists(SLEEP_STATE):
            try:
                with open(SLEEP_STATE) as f:
                    result['state'] = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return result

    def enter_sleep(self, cfg, vlc=None):
        """Enter sleep mode. Returns status dict.

        Args:
            cfg: kj_config dict for logging
            vlc: VLCManager instance to stop VLC processes
        """
        if self.is_sleeping():
            return {'active': True, 'message': 'Already in sleep mode'}

        self._entering = True
        log_message("Sleep mode: entering...", cfg)
        errors = []

        try:
            # Step 1: Stop VLC via VLCManager (before shell script)
            if vlc and vlc.enabled:
                log_message("Sleep mode: stopping VLC instances...", cfg)
                try:
                    vlc.fade_out_filler()
                    vlc.ensure_filler_stopped()
                    vlc.ensure_karaoke_released()
                    vlc.karaoke_active = False
                    vlc.current_playing_path = None
                    # Kill VLC processes so they don't consume resources
                    for role in ('karaoke', 'filler'):
                        proc = vlc.processes.get(role)
                        if proc and proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                    vlc.processes.clear()
                except Exception as e:
                    errors.append(f"VLC stop: {e}")
                    log_message(f"Sleep mode: VLC stop error: {e}", cfg)

            # Step 2: Kill websockify (started as subprocess by app.py)
            try:
                subprocess.run(
                    ['pkill', '-f', 'websockify'],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass  # May not be running

            # Step 3: Run shell script for system-level operations
            log_message("Sleep mode: running sleep-enter.sh...", cfg)
            result = subprocess.run(
                [ENTER_SCRIPT, SLEEP_STATE],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                errors.append(f"sleep-enter.sh failed: {result.stderr}")
                log_message(f"Sleep mode: script error: {result.stderr}", cfg)
            else:
                log_message(f"Sleep mode: script output: {result.stdout}", cfg)

        finally:
            self._entering = False

        sleeping = self.is_sleeping()
        msg = 'Sleep mode active' if sleeping else 'Failed to enter sleep mode'
        if errors:
            msg += f' (warnings: {"; ".join(errors)})'
        log_message(f"Sleep mode: {msg}", cfg)
        return {'active': sleeping, 'message': msg, 'errors': errors}

    def exit_sleep(self, cfg, vlc=None):
        """Exit sleep mode. Returns status dict.

        Args:
            cfg: kj_config dict for logging
            vlc: VLCManager instance to restart VLC processes
        """
        if not self.is_sleeping():
            return {'active': False, 'message': 'Not in sleep mode'}

        self._exiting = True
        log_message("Sleep mode: exiting...", cfg)
        errors = []

        try:
            # Step 1: Run shell script for system-level operations
            log_message("Sleep mode: running sleep-exit.sh...", cfg)
            result = subprocess.run(
                [EXIT_SCRIPT, SLEEP_STATE],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                errors.append(f"sleep-exit.sh failed: {result.stderr}")
                log_message(f"Sleep mode: script error: {result.stderr}", cfg)
            else:
                log_message(f"Sleep mode: script output: {result.stdout}", cfg)

            # Step 2: Restart VLC instances
            if vlc and vlc.enabled:
                log_message("Sleep mode: restarting VLC instances...", cfg)
                try:
                    vlc.restart_instances()
                except Exception as e:
                    errors.append(f"VLC restart: {e}")
                    log_message(f"Sleep mode: VLC restart error: {e}", cfg)

            # Step 3: Restart websockify
            # This is handled by app.py on next restart, or we can signal it.
            # For now, websockify will come back on next kj-controller restart.
            # The user can also manually restart the app if VNC is needed.

        finally:
            self._exiting = False

        sleeping = self.is_sleeping()
        msg = 'System awake' if not sleeping else 'Failed to exit sleep mode'
        if errors:
            msg += f' (warnings: {"; ".join(errors)})'
        log_message(f"Sleep mode: {msg}", cfg)
        return {'active': sleeping, 'message': msg, 'errors': errors}
