"""AudioMonitor: PipeWire profile switching, ffmpeg capture, and MP3 stream serving."""

import os
import subprocess
import threading
import time

from utils import log_message

# PipeWire / PulseAudio constants
PW_CARD = 'alsa_card.pci-0000_00_1f.3'
PW_HDMI_PROFILE = 'output:hdmi-stereo+input:analog-stereo'
PW_ANALOG_PROFILE = 'output:analog-stereo+input:analog-stereo'
PW_MONITOR_SOURCE = 'alsa_output.pci-0000_00_1f.3.hdmi-stereo.monitor'
PACTL_ENV_PREFIX = ['sudo', '-u', 'nomad', 'env', 'XDG_RUNTIME_DIR=/run/user/1000']

STREAM_CHUNK_SIZE = 4096


class AudioMonitor:
    """Captures HDMI audio via PipeWire/ffmpeg and serves it as an MP3 stream."""

    def __init__(self, mpv_manager, config=None):
        self.mpv = mpv_manager
        self.config = config or {}
        self.active = False
        self._ffmpeg_proc = None
        self._drain_thread = None
        self._client_connected = False
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────

    def start(self):
        """Switch to HDMI profile, restart players with PipeWire, start ffmpeg capture."""
        with self._lock:
            if self.active:
                log_message("Audio monitor already active, ignoring start().", self.config)
                return

            # Switch PipeWire card to HDMI profile
            log_message("Switching PipeWire card to HDMI profile...", self.config)
            subprocess.run(
                PACTL_ENV_PREFIX + ['pactl', 'set-card-profile', PW_CARD, PW_HDMI_PROFILE],
                check=False,
            )

            # Switch mpv/VLC to PipeWire audio backend and restart
            self.mpv.audio_backend = 'pipewire'
            self.mpv.restart_instances()

            # Start ffmpeg capturing from PipeWire monitor source
            # Full PACTL_ENV_PREFIX needed — ffmpeg requires XDG_RUNTIME_DIR to find PipeWire
            ffmpeg_cmd = PACTL_ENV_PREFIX + [
                'ffmpeg',
                '-f', 'pulse',
                '-i', PW_MONITOR_SOURCE,
                '-c:a', 'libmp3lame',
                '-b:a', '128k',
                '-f', 'mp3',
                '-fflags', '+nobuffer',
                '-flags', '+low_delay',
                'pipe:1',
            ]
            log_message(f"Starting ffmpeg capture: {' '.join(ffmpeg_cmd)}", self.config)
            ffmpeg_log = None
            try:
                log_dir = os.path.dirname(self.config.get('log_file', ''))
                if log_dir:
                    ffmpeg_log = open(os.path.join(log_dir, 'ffmpeg-monitor.log'), 'a')
            except OSError:
                pass
            self._ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=ffmpeg_log or subprocess.DEVNULL,
            )

            # Start drain thread to prevent pipe backpressure when no client
            self._client_connected = False
            self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
            self._drain_thread.start()

            self.active = True
            log_message("Audio monitor started.", self.config)

    def stop(self):
        """Kill ffmpeg, restore ALSA backend, switch PipeWire back to analog."""
        with self._lock:
            if not self.active:
                return

            # Kill ffmpeg process
            if self._ffmpeg_proc is not None:
                log_message("Stopping ffmpeg capture...", self.config)
                self._ffmpeg_proc.terminate()
                try:
                    self._ffmpeg_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._ffmpeg_proc.kill()
                    self._ffmpeg_proc.wait(timeout=5)
                self._ffmpeg_proc = None

            self.active = False

            # Restore ALSA audio backend and restart players
            self.mpv.audio_backend = 'alsa'
            self.mpv.restart_instances()

            # Switch PipeWire card back to analog profile
            log_message("Switching PipeWire card to analog profile...", self.config)
            subprocess.run(
                PACTL_ENV_PREFIX + ['pactl', 'set-card-profile', PW_CARD, PW_ANALOG_PROFILE],
                check=False,
            )
            log_message("Audio monitor stopped.", self.config)

    def status(self):
        """Return current monitor status dict."""
        if not self.active:
            return {'active': False}
        return {'active': True, 'stream_url': '/audio-monitor/stream'}

    def stream_generator(self):
        """Yield MP3 chunks from ffmpeg stdout. For use with Flask streaming response."""
        proc = self._ffmpeg_proc
        if proc is None or proc.stdout is None:
            return

        self._client_connected = True
        try:
            while True:
                chunk = proc.stdout.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            self._client_connected = False

    # ── Internal ──────────────────────────────────────────────────────

    def _drain_loop(self):
        """Background thread: discard ffmpeg output when no client is connected."""
        while True:
            proc = self._ffmpeg_proc
            if proc is None or proc.stdout is None:
                break

            if self._client_connected:
                # Client is reading — sleep briefly and re-check
                time.sleep(0.1)
                continue

            # No client — drain data to prevent pipe backpressure
            try:
                data = proc.stdout.read(STREAM_CHUNK_SIZE)
                if not data:
                    break
            except (ValueError, OSError):
                # stdout closed
                break
