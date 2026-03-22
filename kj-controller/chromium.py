"""ChromiumManager: launch and manage a fullscreen Chromium browser instance."""

import os
import shutil
import subprocess
import time
from urllib.parse import urlparse

from config import is_pi
from utils import log_message

# Chromium user data dir — separate from any existing sessions
CHROMIUM_DATA_DIR = '/tmp/kj-chromium'

# Candidate binary names in priority order
CHROMIUM_BINARIES = ['chromium-browser', 'chromium', 'google-chrome']

# PipeWire card name for NomadPC (Intel PCH)
PW_CARD = 'alsa_card.pci-0000_00_1f.3'

# Map KJ Controller ALSA device names → PipeWire card profiles.
# The KJ audio_device setting controls which ALSA device VLC uses directly.
# For Chromium (which goes through PipeWire), we set the matching PipeWire
# card profile so audio is routed to the same physical output.
ALSA_TO_PW_PROFILE = {
    'hdmiout': 'output:hdmi-stereo+input:analog-stereo',
    'default': 'output:analog-stereo+input:analog-stereo',
}

# Available PipeWire profiles for the browser audio dropdown.
# Keys are user-facing labels, values are pactl profile strings.
PW_PROFILES = {
    'hdmi': 'output:hdmi-stereo+input:analog-stereo',
    'analog': 'output:analog-stereo+input:analog-stereo',
}

# Safe fallback — analog profile doesn't lock HDMI for VLC
PW_PROFILE_ANALOG = 'output:analog-stereo+input:analog-stereo'


class ChromiumManager:
    """Manages a single fullscreen Chromium instance on the device display."""

    def __init__(self, config):
        self.config = config
        self.process = None
        self.current_url = None
        # Kill any orphan Chromium left from a previous server instance
        # and reset PipeWire so VLC can use HDMI via ALSA
        if self.has_orphan():
            self._kill_orphans()
            self._reset_pipewire()

    def has_orphan(self):
        """Check if any Chromium process is running with our data dir (not managed by us)."""
        try:
            result = subprocess.run(
                ['pgrep', '-f', '--', f'--user-data-dir={CHROMIUM_DATA_DIR}'],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _find_binary(self):
        """Find the first available Chromium binary on the system."""
        for name in CHROMIUM_BINARIES:
            path = shutil.which(name)
            if path:
                return path
        return None

    def _pactl(self, *args):
        """Run a pactl command as the nomad user (PipeWire on NomadPC)."""
        try:
            result = subprocess.run(
                ['sudo', '-u', 'nomad', 'env', 'XDG_RUNTIME_DIR=/run/user/1000',
                 'pactl', *args],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0, result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, ''

    def _set_pipewire_profile(self, audio_device):
        """Set PipeWire card profile for browser audio output.

        Args:
            audio_device: Either an ALSA device name (e.g. 'hdmiout') which is
                         mapped to a PipeWire profile, or a direct PipeWire
                         profile string (e.g. 'output:hdmi-stereo+input:analog-stereo').
        """
        if is_pi():
            return  # Pi doesn't use PipeWire

        # If it looks like a PipeWire profile string, use it directly
        if audio_device and audio_device.startswith('output:'):
            profile = audio_device
        else:
            profile = ALSA_TO_PW_PROFILE.get(audio_device, PW_PROFILE_ANALOG)
        ok, _ = self._pactl('set-card-profile', PW_CARD, profile)
        if ok:
            log_message(f"PipeWire profile set to '{profile}' for browser audio.", self.config)
        else:
            log_message(f"WARNING: Could not set PipeWire profile to '{profile}'.", self.config)

    def _reset_pipewire(self):
        """Reset PipeWire to analog profile so VLC can use HDMI via ALSA directly."""
        if is_pi():
            return
        ok, _ = self._pactl('set-card-profile', PW_CARD, PW_PROFILE_ANALOG)
        if ok:
            log_message("PipeWire reset to analog profile for VLC.", self.config)
        else:
            log_message("WARNING: Could not reset PipeWire to analog profile.", self.config)

    def is_running(self):
        """Check if any Chromium process with our data dir is alive (managed or orphan)."""
        if self.process is not None and self.process.poll() is None:
            return True
        return self.has_orphan()

    def launch(self, url, audio_device=None):
        """Launch Chromium in kiosk mode at the given URL.

        Kills any existing instance first (managed or orphan), then sets the
        PipeWire profile to match the configured audio output device.

        Args:
            url: URL to open in Chromium.
            audio_device: ALSA device name from KJ config (e.g. 'hdmiout').
                         Used to set PipeWire profile for browser audio routing.
        """
        if not url:
            url = 'https://youtube.com'

        # Only allow http/https URLs — block file://, data:, etc.
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            log_message(f"ERROR: Rejected unsafe URL scheme '{parsed.scheme}'.", self.config)
            return False

        binary = self._find_binary()
        if not binary:
            log_message("ERROR: No Chromium binary found on this system.", self.config)
            return False

        # Kill any existing instance first
        self.kill()

        # Set PipeWire profile to match the configured audio device
        if audio_device:
            self._set_pipewire_profile(audio_device)

        command = [
            binary,
            '--kiosk',
            '--no-first-run',
            '--disable-translate',
            '--disable-infobars',
            '--autoplay-policy=no-user-gesture-required',
            f'--user-data-dir={CHROMIUM_DATA_DIR}',
            url,
        ]

        env = os.environ.copy()
        env['DISPLAY'] = ':0'
        if is_pi():
            env['XDG_RUNTIME_DIR'] = '/run/user/1000'

        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
            self.current_url = url
            log_message(
                f"Chromium launched (PID {self.process.pid}) at {url}",
                self.config,
            )
            return True
        except Exception as e:
            log_message(f"ERROR: Failed to launch Chromium: {e}", self.config)
            self.process = None
            # Revert PipeWire on failure
            self._reset_pipewire()
            return False

    def kill(self):
        """Terminate the Chromium process and reset PipeWire to analog."""
        was_running = self.is_running()

        # Kill managed process
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
                log_message(
                    f"Chromium process (PID {self.process.pid}) terminated.",
                    self.config,
                )
            self.process = None

        # Kill any orphan Chromium instances launched with our data dir
        had_orphans = self.has_orphan()
        self._kill_orphans()
        self.current_url = None

        # Reset PipeWire to analog so VLC can use HDMI via ALSA
        if was_running or had_orphans:
            self._reset_pipewire()

    def _kill_orphans(self):
        """Kill any Chromium processes using our user-data-dir."""
        try:
            result = subprocess.run(
                ['pkill', '-f', '--', f'--user-data-dir={CHROMIUM_DATA_DIR}'],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.5)
        except subprocess.TimeoutExpired:
            log_message("WARNING: pkill timed out killing orphan Chromium instances.", self.config)
        except FileNotFoundError:
            pass

    def get_status(self):
        """Return current status as a dict for the /status endpoint."""
        running = self.is_running()
        return {
            'running': running,
            'pid': self.process.pid if running else None,
            'url': self.current_url if running else None,
        }
