"""FillerVLC: shared filler-music process used by both renderer backends.

Runs a dedicated `cvlc` on port 8081 playing a looping filler track.
Both `VlcKaraokePlayer` and `MpvKaraokePlayer` delegate the filler side
of playback here so (a) there's no duplication and (b) the filler keeps
playing uninterrupted across renderer swaps.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time

import requests

from config import APP_DIR, is_pi
from utils import log_message

FILLER_STATE_FILE = '/tmp/kj-filler-state.json'


class FillerVLC:
    """Owns the filler VLC process, its audio backend, and fade transitions."""

    def __init__(self, config, enabled=None, audio_backend='alsa'):
        self.config = config
        self.enabled = enabled if enabled is not None else (is_pi() or config.get('enable_vlc', False))
        self.process = None
        self.current_track = None
        self.volume = config.get('filler_volume', 100)
        self.audio_device = config.get('default_audio_device', 'hdmiout')
        self.audio_backend = audio_backend
        self._fade_cancel = threading.Event()

    # ── State persistence ──────────────────────────────────────────────

    def _save_state(self):
        state = {'current_track': self.current_track}
        try:
            with open(FILLER_STATE_FILE, 'w') as f:
                json.dump(state, f)
        except OSError:
            pass

    def _load_state(self):
        try:
            with open(FILLER_STATE_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    # ── HTTP helpers ───────────────────────────────────────────────────

    @property
    def port(self) -> int:
        return self.config.get('filler_vlc_port', 8081)

    @property
    def password(self) -> str:
        return self.config.get('filler_vlc_password', 'filler')

    def probe(self):
        """Return status dict if filler VLC is reachable, else None."""
        try:
            s = requests.Session()
            s.auth = ('', self.password)
            resp = s.get(f"http://localhost:{self.port}/requests/status.json", timeout=2)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def send(self, command, is_path=False, debug=False):
        """Send a command to the filler VLC HTTP API."""
        if not self.enabled:
            return None

        if '&' in command and not is_path:
            url = f"http://localhost:{self.port}/requests/status.json?command={command}"
        else:
            parts = command.split('&input=', 1)
            cmd_part = parts[0]
            input_part = parts[1] if len(parts) > 1 else ''
            encoded_input = requests.utils.quote(input_part)
            url = f"http://localhost:{self.port}/requests/status.json?command={cmd_part}&input={encoded_input}"

        try:
            s = requests.Session()
            s.auth = ('', self.password)
            response = s.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if debug:
                log_message(f"Error sending command to filler VLC: {e}", self.config)
            return None
        except Exception as e:
            log_message(f"Unexpected error calling filler VLC: {e}", self.config)
            return None

    # ── Process management ─────────────────────────────────────────────

    def _kill_port(self):
        """Kill whatever process is listening on the filler port (orphan cleanup)."""
        try:
            result = subprocess.run(
                ['fuser', f'{self.port}/tcp'], capture_output=True, text=True,
            )
            pids = result.stdout.strip().split()
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (ValueError, OSError):
                    pass
            if pids:
                time.sleep(1)
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ['lsof', '-ti', f':{self.port}'], capture_output=True, text=True,
                )
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except (ValueError, OSError):
                        pass
                if pids and pids[0]:
                    time.sleep(1)
            except FileNotFoundError:
                pass

    def launch(self, media_file=None, loop=True):
        """Launch the filler VLC process. No-op if already running."""
        if not self.enabled:
            return

        if self.process and self.process.poll() is None:
            log_message("Filler VLC already running.", self.config)
            return

        if self.audio_backend == 'pipewire':
            log_message(f"Launching filler VLC on port {self.port} with PipeWire audio...", self.config)
            command = [
                'cvlc',
                '--extraintf', 'http',
                '--http-host', '0.0.0.0',
                '--http-port', str(self.port),
                '--http-password', self.password,
                '--no-video-title-show',
                '--aout', 'pulse',
            ]
        else:
            log_message(
                f"Launching filler VLC on port {self.port} with audio device '{self.audio_device}'...",
                self.config,
            )
            command = [
                'cvlc',
                '--extraintf', 'http',
                '--http-host', '0.0.0.0',
                '--http-port', str(self.port),
                '--http-password', self.password,
                '--no-video-title-show',
                '--aout', 'alsa',
                '--alsa-audio-device', self.audio_device,
            ]
        if media_file:
            command.append(media_file)
        if loop:
            command.append('--loop')

        # On Pi: VLC refuses to run as root; wrap with sudo -u dietpi
        if is_pi():
            wrapper = [
                'sudo', '-u', 'dietpi', 'env',
                'DISPLAY=:0',
                'XDG_RUNTIME_DIR=/run/user/1000',
            ]
            full_command = wrapper + command
        else:
            full_command = command

        vlc_log = None
        try:
            log_dir = os.path.dirname(self.config.get('log_file', '')) or APP_DIR
            vlc_log = open(os.path.join(log_dir, 'vlc-filler.log'), 'a')
            vlc_log.write(f"\n--- filler VLC starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            vlc_log.flush()
            process = subprocess.Popen(
                full_command, stdout=vlc_log, stderr=vlc_log, start_new_session=True,
            )
            self.process = process
            log_message(f"Filler VLC launched with PID {process.pid}.", self.config)
            time.sleep(2)
        except FileNotFoundError:
            log_message("VLC not found — filler instance not launched.", self.config)
            if vlc_log:
                vlc_log.close()

    def shutdown(self):
        """Terminate the filler process and kill any orphan on the port."""
        proc = self.process
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_message("Terminated filler VLC.", self.config)
        else:
            self._kill_port()
        self.process = None

    def try_reconnect(self) -> bool:
        """Reattach to an already-running filler VLC after a service restart."""
        if not self.enabled:
            return False
        status = self.probe()
        if status:
            saved = self._load_state()
            self.current_track = saved.get('current_track', self.current_track)
            log_message(
                f"Reconnected to existing filler VLC (state: {status.get('state')}).",
                self.config,
            )
            return True
        return False

    # ── Fade / play control ────────────────────────────────────────────

    def _fade(self, start_vol, end_vol, duration_s=1.5, cancel_event=None):
        """Linear volume fade. Stops early if cancel_event is set."""
        steps = 20
        delay = duration_s / steps
        for i in range(steps + 1):
            if cancel_event and cancel_event.is_set():
                return
            vol = int(start_vol + (end_vol - start_vol) * (i / steps))
            self.send(f"volume&val={vol}")
            time.sleep(delay)

    def fade_in(self):
        """Start playback at 0 volume and fade up to configured filler volume.

        Also spawns a background thread that detects the "aout dead" failure
        mode (VLC decoder runs but no audio reaches the device — see
        docs/AUDIO.md § Filler Audio Handoff) and auto-heals by relaunching
        the VLC process.
        """
        if not self.enabled:
            return
        # Cancel any in-flight fade before starting a new one
        self._fade_cancel.set()
        self._fade_cancel = threading.Event()
        cancel = self._fade_cancel
        log_message("Fading in filler music...", self.config)
        self.send("volume&val=0")
        self.send("pl_play")
        threading.Thread(
            target=self._fade, args=(0, self.volume),
            kwargs={'cancel_event': cancel},
        ).start()
        threading.Thread(target=self._verify_playing, daemon=True).start()

    # ── Auto-heal ──────────────────────────────────────────────────────

    def _verify_playing(self):
        """Auto-heal: detect dead VLC audio output and relaunch if needed.

        VLC's ALSA aout can fail permanently if the device is busy when it
        initializes. Symptom: state='playing' with decodedaudio growing but
        playedabuffers=0 — audio is decoded into /dev/null. VLC doesn't
        retry aout on its own; only a fresh process recovers.
        """
        time.sleep(4)
        status = self.probe()
        if not status or status.get('state') != 'playing':
            return

        stats = status.get('stats') or {}
        played = stats.get('playedabuffers', 0) or 0
        decoded = stats.get('decodedaudio', 0) or 0
        if played > 0:
            return
        if decoded < 100:
            return

        log_message(
            f"Filler VLC aout not outputting (decoded={decoded}, played=0) — "
            "relaunching filler process to recover.",
            self.config,
        )
        self._relaunch()

    def _relaunch(self):
        """Kill and relaunch the filler VLC process. Plays at target volume
        directly so there's no recursion into fade_in (which would retrigger
        auto-heal indefinitely on hard failures)."""
        self.shutdown()
        time.sleep(0.5)
        self.launch(media_file=self.current_media_path(), loop=True)
        self.send(f"volume&val={self.volume}")
        self.send("pl_play")
        log_message("Filler VLC relaunched.", self.config)

    def fade_out(self):
        """Fade volume to 0 and stop playback (releases the audio device)."""
        if not self.enabled:
            return
        self._fade_cancel.set()
        self._fade_cancel = threading.Event()
        log_message("Fading out filler music...", self.config)
        status = self.send("")
        current_vol = status.get('volume', self.volume) if status else self.volume
        self._fade(current_vol, 0)
        self.send("pl_stop")
        log_message("Filler music faded out and stopped.", self.config)

    def ensure_stopped(self) -> bool:
        """Block until filler VLC confirms state=stopped (5 attempts)."""
        for _ in range(5):
            status = self.send("")
            if status and status.get('state') == 'stopped':
                return True
            self.send("pl_stop")
            time.sleep(0.5)
        log_message("WARNING: Could not confirm filler VLC stopped after 5 attempts", self.config)
        return False

    def set_volume(self, vlc_level: int):
        """Set live filler volume (VLC scale) and persist."""
        self.volume = int(vlc_level)
        self.send(f"volume&val={int(vlc_level)}")

    # ── Convenience ────────────────────────────────────────────────────

    def current_media_path(self) -> str:
        """Return the absolute path to the current filler track, or ''."""
        filler_dir = self.config.get('filler_music_dir', '')
        if filler_dir and self.current_track:
            return os.path.join(filler_dir, self.current_track)
        return ''
