"""ChromiumManager: launch and manage a fullscreen Chromium browser instance."""

import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import quote, urlparse

from config import is_pi
from utils import log_message

# Chromium user data dir — separate from any existing sessions
CHROMIUM_DATA_DIR = '/tmp/kj-chromium'

# CDP remote debugging port for navigating without relaunch
CDP_PORT = 9222

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

# YouTube URL patterns
_YOUTUBE_RE = re.compile(
    r'(youtube\.com/watch|youtube\.com/shorts/|youtu\.be/|youtube\.com/embed/)',
    re.IGNORECASE,
)

# JS injected into YouTube pages to maximize video and set volume to max.
# Retries every 500ms until the video element is ready (max 20s).
_YOUTUBE_SETUP_JS = """
(function() {
    var attempts = 0, maxAttempts = 40;
    function setup() {
        if (++attempts > maxAttempts) return;
        var video = document.querySelector('video');
        if (!video || video.readyState < 2) { setTimeout(setup, 500); return; }
        video.volume = 1;
        video.muted = false;
        var fsBtn = document.querySelector('.ytp-fullscreen-button');
        if (fsBtn && !document.fullscreenElement) {
            fsBtn.click();
        } else if (!fsBtn) {
            setTimeout(setup, 500);
        }
    }
    setup();
})()
"""


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
            f'--remote-debugging-port={CDP_PORT}',
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
            self._youtube_auto_setup(url)
            return True
        except Exception as e:
            log_message(f"ERROR: Failed to launch Chromium: {e}", self.config)
            self.process = None
            # Revert PipeWire on failure
            self._reset_pipewire()
            return False

    def navigate(self, url):
        """Navigate the existing Chromium instance to a new URL via CDP.

        Uses the Chrome DevTools Protocol HTTP API to open a new tab at the
        target URL, then closes the old tab. This avoids the flicker and delay
        of killing and relaunching the entire browser.

        Returns True on success, False if navigation failed (caller should
        fall back to launch()).
        """
        if not url:
            return False

        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            log_message(f"ERROR: Rejected unsafe URL scheme '{parsed.scheme}'.", self.config)
            return False

        if not self.is_running():
            return False

        try:
            conn = http.client.HTTPConnection('localhost', CDP_PORT, timeout=3)

            # Get current tab ID
            conn.request('GET', '/json')
            resp = conn.getresponse()
            pages = json.loads(resp.read())
            if not pages:
                return False
            old_id = pages[0]['id']

            # Open new tab at target URL
            conn.request('PUT', f'/json/new?{quote(url, safe="")}')
            resp = conn.getresponse()
            resp.read()

            # Close old tab
            conn.request('GET', f'/json/close/{old_id}')
            resp = conn.getresponse()
            resp.read()
            conn.close()

            self.current_url = url
            log_message(f"Chromium navigated to {url}", self.config)
            self._youtube_auto_setup(url)
            return True
        except Exception as e:
            log_message(f"WARNING: CDP navigate failed ({e}), will relaunch.", self.config)
            return False

    @staticmethod
    def _is_youtube_video(url):
        """Check if a URL is a YouTube video/shorts/embed URL."""
        return bool(_YOUTUBE_RE.search(url or ''))

    def _cdp_evaluate(self, expression):
        """Evaluate a JavaScript expression in the active tab via CDP WebSocket.

        Uses a minimal raw WebSocket implementation (no external deps) to send
        a single Runtime.evaluate command and read the response.
        """
        # Get the WebSocket debugger URL for the first tab
        conn = http.client.HTTPConnection('localhost', CDP_PORT, timeout=3)
        conn.request('GET', '/json')
        resp = conn.getresponse()
        pages = json.loads(resp.read())
        conn.close()
        if not pages:
            return None
        ws_url = pages[0].get('webSocketDebuggerUrl', '')
        if not ws_url:
            return None

        # Parse ws://host:port/path
        parsed = urlparse(ws_url)
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path

        sock = socket.create_connection((host, port), timeout=10)
        try:
            # WebSocket handshake
            key = base64.b64encode(os.urandom(16)).decode()
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            sock.sendall(handshake.encode())

            # Read upgrade response
            buf = b''
            while b'\r\n\r\n' not in buf:
                buf += sock.recv(4096)

            # Send Runtime.evaluate as masked text frame
            msg = json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression},
            })
            payload = msg.encode()
            mask_key = os.urandom(4)
            frame = bytearray([0x81])  # FIN + TEXT opcode
            length = len(payload)
            if length < 126:
                frame.append(0x80 | length)
            elif length < 65536:
                frame.append(0x80 | 126)
                frame.extend(struct.pack('!H', length))
            else:
                frame.append(0x80 | 127)
                frame.extend(struct.pack('!Q', length))
            frame.extend(mask_key)
            frame.extend(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            sock.sendall(frame)

            # Read response frame
            header = sock.recv(2)
            resp_len = header[1] & 0x7F
            if resp_len == 126:
                resp_len = struct.unpack('!H', sock.recv(2))[0]
            elif resp_len == 127:
                resp_len = struct.unpack('!Q', sock.recv(8))[0]
            data = b''
            while len(data) < resp_len:
                data += sock.recv(resp_len - len(data))
            return json.loads(data.decode())
        finally:
            sock.close()

    def _youtube_auto_setup(self, url):
        """If URL is a YouTube video, inject JS to maximize and set volume to max.

        Runs in a background thread so it doesn't block the navigate/launch call.
        Waits for CDP to be ready, then injects the setup script.
        """
        if not self._is_youtube_video(url):
            return

        def _run():
            # Wait for CDP and page to be ready
            for attempt in range(10):
                time.sleep(2)
                if not self.is_running():
                    return
                try:
                    result = self._cdp_evaluate(_YOUTUBE_SETUP_JS)
                    if result:
                        log_message("YouTube auto-setup: volume max + fullscreen injected.", self.config)
                        return
                except Exception:
                    pass
            log_message("WARNING: YouTube auto-setup failed after retries.", self.config)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

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
