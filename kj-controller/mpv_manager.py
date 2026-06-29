"""MpvKaraokePlayer: mpv karaoke backend with rubberband pitch shifting.

Implements the `KaraokePlayer` protocol. Filler music is owned by a
shared `FillerVLC` instance (see `filler.py`) that this player does NOT
manage — the coordinator wires it in.

Why mpv: the rubberband audio filter enables glitch-free real-time pitch
shifting (±6 semitones) via IPC. `--ao=alsa` is the default audio path.
When the audio monitor is active, the coordinator flips the audio backend
to 'pipewire' which routes through PulseAudio compat for parec capture.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time

from config import APP_DIR, is_pi
from filler import FillerVLC
from utils import log_message

STATE_FILE = '/tmp/kj-mpv-state.json'
MPV_SOCKET_PATH = '/tmp/mpv-karaoke.sock'
PITCH_MIN = -6
PITCH_MAX = 6


def _audio_file_option(audio_file):
    """Build the mpv ``loadfile`` option that attaches an external audio track.

    Used for CDG zips: mpv plays the .cdg (graphics) as the main file and the
    matching .mp3 as an external audio track. mpv's loadfile options are a
    comma-separated ``key=value`` list, so a path containing a comma would
    corrupt parsing — use mpv's length-prefixed value escaping (``%n%value``,
    where n is the byte length) which is literal and safe for any path.
    """
    n = len(audio_file.encode('utf-8'))
    return f"audio-file=%{n}%{audio_file}"


class MpvKaraokePlayer:
    """Karaoke backend that uses mpv + rubberband via IPC."""

    name = 'mpv'
    supports_pitch = True
    supports_cdg = True  # mpv#3027 closed; may still need per-file testing

    def __init__(self, config, filler: FillerVLC, enabled=None, audio_backend='alsa'):
        self.config = config
        self.enabled = enabled if enabled is not None else (is_pi() or config.get('enable_vlc', False))
        self.filler = filler
        self.process = None
        self.current_path = None
        self.karaoke_volume = config.get('karaoke_volume', 200)
        self.active = False
        self.last_seek_time = 0
        self.last_play_time = 0
        self.audio_error = False
        self.audio_device = config.get('default_audio_device', 'hdmiout')
        self.audio_backend = audio_backend
        self._play_lock = threading.Lock()
        self.on_karaoke_end = None
        self._pitch_semitones = 0
        self.ipc_socket_path = MPV_SOCKET_PATH
        self._ipc_lock = threading.Lock()
        self._monitor_stop = threading.Event()

    # Protocol surface
    @property
    def volume(self) -> int:
        return self.karaoke_volume

    @property
    def pitch_semitones(self) -> int:
        return self._pitch_semitones

    # ── IPC ────────────────────────────────────────────────────────────

    def _send_ipc(self, command):
        """Send a JSON command to mpv's IPC socket. Returns parsed response or None."""
        with self._ipc_lock:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect(self.ipc_socket_path)
                msg = json.dumps({"command": command}) + "\n"
                s.sendall(msg.encode())
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                s.close()
                if buf:
                    for line in buf.split(b"\n"):
                        if not line.strip():
                            continue
                        parsed = json.loads(line)
                        if "request_id" in parsed:
                            return parsed
                    return json.loads(buf.split(b"\n")[0])
                return None
            except (OSError, json.JSONDecodeError, ConnectionRefusedError):
                return None

    def _get_property(self, name):
        resp = self._send_ipc(["get_property", name])
        if resp and resp.get("error") == "success":
            return resp.get("data")
        return None

    def _set_property(self, name, value):
        resp = self._send_ipc(["set_property", name, value])
        return resp is not None and resp.get("error") == "success"

    def _mpv_is_running(self):
        return self._get_property("idle-active") is not None

    # ── Volume scale conversion ────────────────────────────────────────

    @staticmethod
    def _vlc_to_mpv_volume(vlc_vol):
        """Convert VLC volume (0-512, 256=100%) to mpv volume (0-200, 100=100%)."""
        return (vlc_vol / 256) * 100

    # ── State persistence ──────────────────────────────────────────────

    def _save_state(self):
        state = {'current_playing_path': self.current_path}
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
        except OSError:
            pass

    def _load_state(self):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    # ── Lifecycle ──────────────────────────────────────────────────────

    def launch(self):
        """Start mpv in idle mode with rubberband filter and IPC socket."""
        if not self.enabled:
            return

        if self.process and self.process.poll() is None:
            log_message("mpv karaoke already running.", self.config)
            return

        try:
            os.unlink(self.ipc_socket_path)
        except FileNotFoundError:
            pass

        if self.audio_backend == 'pipewire':
            log_message("Launching mpv karaoke with PulseAudio/PipeWire audio...", self.config)
            command = [
                'mpv', '--idle',
                '--fs',
                '--ao=pulse',
                '--af=@rb:rubberband',
                f'--input-ipc-server={self.ipc_socket_path}',
                '--really-quiet',
                '--keep-open=no',
                '--no-input-default-bindings',
                '--no-osc',
            ]
        else:
            log_message(
                f"Launching mpv karaoke with audio device 'alsa/{self.audio_device}'...",
                self.config,
            )
            command = [
                'mpv', '--idle',
                '--fs',
                '--ao=alsa',
                f'--audio-device=alsa/{self.audio_device}',
                '--af=@rb:rubberband',
                f'--input-ipc-server={self.ipc_socket_path}',
                '--really-quiet',
                '--keep-open=no',
                '--no-input-default-bindings',
                '--no-osc',
            ]

        mpv_log = None
        try:
            log_dir = os.path.dirname(self.config.get('log_file', '')) or APP_DIR
            mpv_log = open(os.path.join(log_dir, 'mpv-karaoke.log'), 'a')
            mpv_log.write(f"\n--- mpv karaoke starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            mpv_log.flush()
            process = subprocess.Popen(
                command, stdout=mpv_log, stderr=mpv_log, start_new_session=True,
            )
            self.process = process
            log_message(f"mpv karaoke launched with PID {process.pid}.", self.config)
            for _ in range(20):
                time.sleep(0.25)
                if os.path.exists(self.ipc_socket_path):
                    if self._mpv_is_running():
                        break
            else:
                log_message("WARNING: mpv IPC socket not ready after 5 seconds.", self.config)
        except FileNotFoundError:
            log_message("mpv not found — karaoke instance not launched.", self.config)
            if mpv_log:
                mpv_log.close()

    def try_reconnect(self) -> bool:
        if not self.enabled:
            return False
        if not os.path.exists(self.ipc_socket_path):
            return False
        idle = self._get_property("idle-active")
        if idle is None:
            return False
        is_idle = idle is True
        if not is_idle:
            saved = self._load_state()
            self.active = True
            self.current_path = saved.get('current_playing_path')
            self.last_play_time = time.time()
            log_message("Reconnected to existing mpv karaoke (playing).", self.config)
        else:
            log_message("Reconnected to existing mpv karaoke (idle).", self.config)
        return True

    def shutdown(self):
        """Terminate mpv. Used during renderer swap."""
        self._monitor_stop.set()
        self._send_ipc(["quit"])
        time.sleep(0.5)
        proc = self.process
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_message("Terminated mpv karaoke.", self.config)
        self.process = None
        self.active = False
        self.current_path = None
        self._pitch_semitones = 0
        try:
            os.unlink(self.ipc_socket_path)
        except FileNotFoundError:
            pass

    # ── Pitch control ──────────────────────────────────────────────────

    def set_pitch(self, semitones):
        """Set pitch in semitones. Real-time; no interruption during playback."""
        semitones = max(PITCH_MIN, min(PITCH_MAX, int(semitones)))
        self._pitch_semitones = semitones
        if self.active:
            pitch_scale = 2 ** (semitones / 12)
            self._send_ipc(["af-command", "rb", "set-pitch", str(pitch_scale)])

    def _apply_pitch(self):
        """Re-apply current pitch_semitones to the rubberband filter."""
        pitch_scale = 2 ** (self._pitch_semitones / 12)
        self._send_ipc(["af-command", "rb", "set-pitch", str(pitch_scale)])

    # ── Playback control ───────────────────────────────────────────────

    def play(self, file_path, display_path=None, overlay_manager=None, audio_file=None):
        if not os.path.exists(file_path):
            log_message(f"ERROR: File not found: {file_path}", self.config)
            return

        if not self.enabled:
            log_message(f"mpv disabled — cannot play {os.path.basename(file_path)}", self.config)
            return

        with self._play_lock:
            self.last_play_time = time.time()
            self.audio_error = False
            self._pitch_semitones = 0  # reset for each new song

            if display_path is not None:
                self.current_path = display_path
            if overlay_manager is not None:
                overlay_manager.set_karaoke_playing(True)

            load_cmd = ["loadfile", file_path, "replace"]
            if audio_file:
                # mpv 0.37: loadfile <url> <flags> <options-string>.
                load_cmd.append(_audio_file_option(audio_file))
            resp = self._send_ipc(load_cmd)
            if resp is None or resp.get("error") != "success":
                log_message(f"ERROR: mpv failed to load {file_path}", self.config)
                self.audio_error = True
                if overlay_manager is not None:
                    overlay_manager.set_karaoke_playing(False)
                self.current_path = None
                return

            time.sleep(0.5)
            mpv_vol = self._vlc_to_mpv_volume(self.karaoke_volume)
            self._set_property("volume", mpv_vol)

            self.active = True
            self._save_state()
            log_message(f"Playback started for {os.path.basename(file_path)}.", self.config)

        def verify():
            time.sleep(3)
            if not self.active:
                return
            time_pos = self._get_property("time-pos")
            if time_pos is not None and time_pos > 0:
                self.audio_error = False
            else:
                log_message("WARNING: mpv not progressing — possible audio device issue", self.config)
                self.audio_error = True

        threading.Thread(target=verify, daemon=True).start()

    def stop(self):
        self._send_ipc(["stop"])
        self.active = False
        self.current_path = None
        self.audio_error = False
        self._save_state()

    def seek(self, seconds):
        self.last_seek_time = time.time()
        self._send_ipc(["seek", int(seconds), "absolute"])

    def pause_resume(self):
        current = self._get_property("pause")
        if current is None:
            return None
        new_state = not current
        self._set_property("pause", new_state)
        return new_state

    def set_volume(self, vlc_level):
        self.karaoke_volume = int(vlc_level)
        mpv_vol = self._vlc_to_mpv_volume(vlc_level)
        self._set_property("volume", mpv_vol)

    def fadeout(self, duration_s=3.0):
        """Fade mpv volume to 0 over duration_s, stop, restore configured volume."""
        saved_volume = self.karaoke_volume
        steps = 20
        delay = duration_s / steps
        mpv_start = self._vlc_to_mpv_volume(saved_volume)

        def _do():
            for i in range(steps + 1):
                vol = mpv_start * (1 - i / steps)
                self._set_property("volume", vol)
                time.sleep(delay)
            self.stop()
            self.karaoke_volume = saved_volume
            log_message(f"Fadeout complete, volume restored to {saved_volume}.", self.config)

        threading.Thread(target=_do, daemon=True).start()

    def get_status(self):
        if not self.active:
            return {"state": "stopped", "time": 0, "length": 0}

        paused = self._get_property("pause")
        time_pos = self._get_property("time-pos")
        duration = self._get_property("duration")

        if paused is None and time_pos is None:
            return {"state": "stopped", "time": 0, "length": 0}

        return {
            "state": "paused" if paused else "playing",
            "time": int(time_pos or 0),
            "length": int(duration or 0),
        }

    def ensure_released(self) -> bool:
        """Force mpv to fully idle and release the ALSA device.

        mpv emits end-file ~350ms before it actually closes ALSA. Callers
        reclaiming the audio device (e.g. filler fade_in) must wait for
        this. See docs/AUDIO.md § Filler Audio Handoff.
        """
        self._send_ipc(["stop"])
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if self._get_property("idle-active") is True:
                break
            time.sleep(0.02)
        # idle-active flips true slightly before ALSA draining completes
        time.sleep(0.15)
        return True

    # ── Monitor ────────────────────────────────────────────────────────

    def monitor(self):
        """Listen for mpv IPC events; fire on_karaoke_end on EOF.

        Uses a persistent socket connection. Falls back to polling if the
        socket can't be established. Exits cleanly when _monitor_stop is set.
        """
        while not self._monitor_stop.is_set():
            try:
                self._monitor_via_events()
            except Exception as e:
                log_message(
                    f"mpv monitor event loop error: {e}, falling back to polling",
                    self.config,
                )
                self._monitor_via_polling()
            if self._monitor_stop.is_set():
                return

    def _monitor_via_events(self):
        while not self._monitor_stop.is_set():
            if not os.path.exists(self.ipc_socket_path):
                time.sleep(2)
                continue

            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect(self.ipc_socket_path)
                s.settimeout(None)
            except OSError:
                time.sleep(2)
                continue

            log_message("mpv monitor connected to IPC socket.", self.config)
            buf = b""
            try:
                while not self._monitor_stop.is_set():
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("event") == "end-file" and msg.get("reason") == "eof":
                            self._handle_karaoke_ended()
            except OSError:
                pass
            finally:
                s.close()
                time.sleep(1)

    def _monitor_via_polling(self):
        while not self._monitor_stop.is_set():
            time.sleep(2)
            if not self.active:
                continue

            if time.time() - self.last_seek_time < 5:
                continue
            if time.time() - self.last_play_time < 5:
                continue

            idle = self._get_property("idle-active")
            if idle is True:
                self._handle_karaoke_ended()

    def _handle_karaoke_ended(self):
        if not self.active:
            return
        log_message("Karaoke video finished playing.", self.config)
        self.active = False
        self.current_path = None
        self._pitch_semitones = 0
        self._save_state()
        # Ensure ALSA is released BEFORE filler reclaims it (mpv race fix)
        self.ensure_released()
        if self.on_karaoke_end:
            try:
                self.on_karaoke_end()
            except Exception:
                pass
