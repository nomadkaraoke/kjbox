"""MpvManager: mpv (karaoke) + VLC (filler) playback with real-time pitch control."""

import json
import os
import signal
import socket
import subprocess
import threading
import time

import requests

from config import APP_DIR, is_pi
from utils import log_message

STATE_FILE = '/tmp/kj-vlc-state.json'
MPV_SOCKET_PATH = '/tmp/mpv-karaoke.sock'
PITCH_MIN = -6
PITCH_MAX = 6


class MpvManager:
    """Manages mpv for karaoke playback (with rubberband pitch shifting) and VLC for filler music."""

    def __init__(self, config, enabled=None):
        self.config = config
        self.enabled = enabled if enabled is not None else (is_pi() or config.get('enable_vlc', False))
        self.processes = {"karaoke": None, "filler": None}
        self.current_playing_path = None
        self.current_filler_track = None
        self.filler_volume = config.get('filler_volume', 100)
        self.karaoke_volume = config.get('karaoke_volume', 200)
        self.karaoke_active = False
        self.last_seek_time = 0
        self.last_play_time = 0
        self.audio_error = False
        self.audio_device = config.get('default_audio_device', 'hdmiout')
        self.audio_backend = 'alsa'
        self._play_lock = threading.Lock()
        self._fade_cancel = threading.Event()
        self.on_karaoke_end = None

        # mpv-specific state
        self.pitch_semitones = 0
        self.ipc_socket_path = MPV_SOCKET_PATH
        self._ipc_lock = threading.Lock()

    # ── IPC communication ──────────────────────────────────────────────

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
                    # Parse the first complete line (skip any event lines)
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
        """Get an mpv property value. Returns the value or None on error."""
        resp = self._send_ipc(["get_property", name])
        if resp and resp.get("error") == "success":
            return resp.get("data")
        return None

    def _set_property(self, name, value):
        """Set an mpv property. Returns True on success."""
        resp = self._send_ipc(["set_property", name, value])
        return resp is not None and resp.get("error") == "success"

    # ── Volume scale conversion ────────────────────────────────────────

    @staticmethod
    def _vlc_to_mpv_volume(vlc_vol):
        """Convert VLC volume (0-512, 256=100%) to mpv volume (0-200, 100=100%)."""
        return (vlc_vol / 256) * 100

    # ── Pitch control ──────────────────────────────────────────────────

    def set_pitch(self, semitones):
        """Set pitch offset in semitones. Real-time, no interruption during playback.

        af-command only works while audio is playing. If nothing is playing,
        we store the value and apply it when the next song starts.
        """
        semitones = max(PITCH_MIN, min(PITCH_MAX, int(semitones)))
        self.pitch_semitones = semitones
        if self.karaoke_active:
            pitch_scale = 2 ** (semitones / 12)
            self._send_ipc(["af-command", "rb", "set-pitch", str(pitch_scale)])

    def _apply_pitch(self):
        """Apply current pitch_semitones to the rubberband filter. Call after playback starts."""
        pitch_scale = 2 ** (self.pitch_semitones / 12)
        self._send_ipc(["af-command", "rb", "set-pitch", str(pitch_scale)])

    # ── State persistence ──────────────────────────────────────────────

    def _save_state(self):
        """Persist playback state so it can be recovered after a restart."""
        state = {
            'current_playing_path': self.current_playing_path,
            'current_filler_track': self.current_filler_track,
        }
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
        except OSError:
            pass

    def _load_state(self):
        """Load persisted playback state if available."""
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    # ── Filler VLC management (unchanged from VLCManager) ──────────────

    def _probe_vlc(self, port, password):
        """Check if a VLC instance is responding on a port. Returns status dict or None."""
        try:
            s = requests.Session()
            s.auth = ('', password)
            resp = s.get(f"http://localhost:{port}/requests/status.json", timeout=2)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _kill_port(self, port):
        """Kill whatever process is listening on a TCP port."""
        try:
            result = subprocess.run(
                ['fuser', f'{port}/tcp'],
                capture_output=True, text=True
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
                    ['lsof', '-ti', f':{port}'],
                    capture_output=True, text=True
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

    def send_command(self, port, password, command, is_path=False, debug=False):
        """Sends a command to a VLC HTTP interface (filler instance only)."""
        if not self.enabled:
            return None

        if '&' in command and not is_path:
            url = f"http://localhost:{port}/requests/status.json?command={command}"
        else:
            parts = command.split('&input=', 1)
            cmd_part = parts[0]
            input_part = parts[1] if len(parts) > 1 else ''
            encoded_input = requests.utils.quote(input_part)
            url = f"http://localhost:{port}/requests/status.json?command={cmd_part}&input={encoded_input}"

        try:
            s = requests.Session()
            s.auth = ('', password)
            response = s.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if debug:
                log_message(f"Error sending command to VLC on port {port}: {e}", self.config)
            return None
        except Exception as e:
            log_message(f"An unexpected error occurred when calling VLC: {e}", self.config)
            return None

    def fade_music(self, port, password, start_vol, end_vol, duration_s=1.5, cancel_event=None):
        """Gradually fades filler VLC volume over a set duration."""
        steps = 20
        delay = duration_s / steps
        for i in range(steps + 1):
            if cancel_event and cancel_event.is_set():
                return
            volume = int(start_vol + (end_vol - start_vol) * (i / steps))
            self.send_command(port, password, f"volume&val={volume}")
            time.sleep(delay)

    def fade_in_filler(self):
        """Fades in the filler music. Cancels any in-progress fade-out first."""
        if not self.enabled:
            return
        self._fade_cancel.set()
        self._fade_cancel = threading.Event()
        cancel = self._fade_cancel
        log_message("Fading in filler music...", self.config)
        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')
        self.send_command(filler_port, filler_pw, "volume&val=0")
        self.send_command(filler_port, filler_pw, "pl_play")
        threading.Thread(target=self.fade_music, args=(filler_port, filler_pw, 0, self.filler_volume),
                         kwargs={'cancel_event': cancel}).start()

    def fade_out_filler(self):
        """Fades out the filler music and stops it (releases audio device for karaoke)."""
        if not self.enabled:
            return
        self._fade_cancel.set()
        self._fade_cancel = threading.Event()
        log_message("Fading out filler music...", self.config)
        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')
        status = self.send_command(filler_port, filler_pw, "")
        current_vol = status.get('volume', self.filler_volume) if status else self.filler_volume
        self.fade_music(filler_port, filler_pw, current_vol, 0)
        self.send_command(filler_port, filler_pw, "pl_stop")
        log_message("Filler music faded out and stopped.", self.config)

    def ensure_filler_stopped(self):
        """Verifies filler VLC has released the audio device."""
        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')
        for attempt in range(5):
            status = self.send_command(filler_port, filler_pw, "")
            if status and status.get('state') == 'stopped':
                return True
            self.send_command(filler_port, filler_pw, "pl_stop")
            time.sleep(0.5)
        log_message("WARNING: Could not confirm filler VLC stopped after 5 attempts", self.config)
        return False

    # ── Karaoke mpv management ─────────────────────────────────────────

    def _mpv_is_running(self):
        """Check if the mpv karaoke instance is reachable via IPC."""
        return self._get_property("idle-active") is not None

    def ensure_karaoke_released(self):
        """Stops mpv karaoke playback and verifies it returns to idle."""
        self._send_ipc(["stop"])
        for attempt in range(5):
            idle = self._get_property("idle-active")
            if idle is True:
                return True
            self._send_ipc(["stop"])
            time.sleep(0.5)
        log_message("WARNING: Could not confirm mpv karaoke returned to idle after 5 attempts", self.config)
        return False

    def get_karaoke_status(self):
        """Get current karaoke playback status for the /status endpoint.

        Returns a dict matching the fields that routes.py expects from VLC's status.json:
        state, time, length.
        """
        if not self.karaoke_active:
            return {"state": "stopped", "time": 0, "length": 0}

        paused = self._get_property("pause")
        time_pos = self._get_property("time-pos")
        duration = self._get_property("duration")

        if paused is None and time_pos is None:
            # mpv not responding — probably crashed or not playing
            return {"state": "stopped", "time": 0, "length": 0}

        return {
            "state": "paused" if paused else "playing",
            "time": int(time_pos or 0),
            "length": int(duration or 0),
        }

    def seek_karaoke(self, seconds):
        """Seek to absolute position in seconds."""
        self.last_seek_time = time.time()
        self._send_ipc(["seek", int(seconds), "absolute"])

    def pause_resume_karaoke(self):
        """Toggle pause on the karaoke mpv instance. Returns new pause state."""
        current = self._get_property("pause")
        if current is None:
            return None
        new_state = not current
        self._set_property("pause", new_state)
        return new_state

    def set_karaoke_volume_live(self, vlc_level):
        """Set karaoke volume. Accepts VLC-scale value (0-512), converts for mpv."""
        self.karaoke_volume = vlc_level
        mpv_vol = self._vlc_to_mpv_volume(vlc_level)
        self._set_property("volume", mpv_vol)

    def stop_karaoke(self):
        """Stop karaoke playback, return mpv to idle."""
        self._send_ipc(["stop"])
        self.karaoke_active = False
        self.current_playing_path = None
        self.audio_error = False
        self._save_state()

    # ── Instance lifecycle ─────────────────────────────────────────────

    def launch_instance(self, name, port=None, password=None, media_file=None, loop=False):
        """Launch an mpv (karaoke) or VLC (filler) instance."""
        if not self.enabled:
            return

        if name == "karaoke":
            self._launch_mpv_karaoke()
        elif name == "filler":
            self._launch_vlc_filler(port, password, media_file, loop)

    def _launch_mpv_karaoke(self):
        """Launch mpv in idle mode with rubberband filter and IPC socket."""
        proc = self.processes.get("karaoke")
        if proc and proc.poll() is None:
            log_message("mpv karaoke instance is already running.", self.config)
            return

        # Clean up stale socket
        try:
            os.unlink(self.ipc_socket_path)
        except FileNotFoundError:
            pass

        if self.audio_backend == 'pipewire':
            log_message("Launching mpv karaoke with PipeWire audio...", self.config)
            command = [
                'mpv', '--idle',
                '--fs',
                '--ao=pipewire',
                '--af=@rb:rubberband',
                f'--input-ipc-server={self.ipc_socket_path}',
                '--really-quiet',
                '--keep-open=no',
                '--no-input-default-bindings',
                '--no-osc',
            ]
        else:
            log_message(f"Launching mpv karaoke with audio device 'alsa/{self.audio_device}'...", self.config)
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
            process = subprocess.Popen(command, stdout=mpv_log, stderr=mpv_log,
                                       start_new_session=True)
            self.processes["karaoke"] = process
            log_message(f"mpv karaoke launched with PID {process.pid}.", self.config)
            # Wait for IPC socket to become available
            for _ in range(20):
                time.sleep(0.25)
                if os.path.exists(self.ipc_socket_path):
                    if self._mpv_is_running():
                        break
            else:
                log_message("WARNING: mpv IPC socket not ready after 5 seconds.", self.config)
        except FileNotFoundError:
            log_message("mpv not found - karaoke instance not launched.", self.config)
            if mpv_log:
                mpv_log.close()

    def _launch_vlc_filler(self, port, password, media_file=None, loop=False):
        """Launch a VLC instance for filler music (identical to VLCManager)."""
        proc = self.processes.get("filler")
        if proc and proc.poll() is None:
            log_message("VLC filler instance is already running.", self.config)
            return

        port = port or self.config.get('filler_vlc_port', 8081)
        password = password or self.config.get('filler_vlc_password', 'filler')

        if self.audio_backend == 'pipewire':
            log_message(f"Launching VLC filler on port {port} with PipeWire audio...", self.config)
            command = [
                'cvlc',
                '--extraintf', 'http',
                '--http-host', '0.0.0.0',
                '--http-port', str(port),
                '--http-password', password,
                '--no-video-title-show',
                '--aout', 'pulse',
            ]
        else:
            log_message(f"Launching VLC filler on port {port} with audio device '{self.audio_device}'...", self.config)
            command = [
                'cvlc',
                '--extraintf', 'http',
                '--http-host', '0.0.0.0',
                '--http-port', str(port),
                '--http-password', password,
                '--no-video-title-show',
                '--aout', 'alsa',
                '--alsa-audio-device', self.audio_device,
            ]
        if media_file:
            command.append(media_file)
        if loop:
            command.extend(['--loop'])

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
            vlc_log.write(f"\n--- VLC filler starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            vlc_log.flush()
            process = subprocess.Popen(full_command, stdout=vlc_log, stderr=vlc_log,
                                       start_new_session=True)
            self.processes["filler"] = process
            log_message(f"VLC filler launched with PID {process.pid}.", self.config)
            time.sleep(2)
        except FileNotFoundError:
            log_message("VLC not found - filler instance not launched.", self.config)
            if vlc_log:
                vlc_log.close()

    def try_reconnect(self):
        """Attempt to reconnect to existing mpv/VLC instances after a restart."""
        if not self.enabled:
            return {'karaoke': False, 'filler': False}

        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')

        found = {'karaoke': False, 'filler': False}
        saved = self._load_state()

        # Check mpv karaoke via IPC socket
        if os.path.exists(self.ipc_socket_path):
            idle = self._get_property("idle-active")
            if idle is not None:
                found['karaoke'] = True
                is_idle = idle is True
                if not is_idle:
                    self.karaoke_active = True
                    self.current_playing_path = saved.get('current_playing_path')
                    self.last_play_time = time.time()
                    log_message(f"Reconnected to existing mpv karaoke (playing).", self.config)
                else:
                    log_message("Reconnected to existing mpv karaoke (idle).", self.config)

        # Check filler VLC
        filler_status = self._probe_vlc(filler_port, filler_pw)
        if filler_status:
            found['filler'] = True
            self.current_filler_track = saved.get('current_filler_track', self.current_filler_track)
            log_message(f"Reconnected to existing filler VLC (state: {filler_status.get('state')}).", self.config)

        return found

    def restart_instances(self):
        """Terminates both processes and relaunches them with the current audio device."""
        if not self.enabled:
            return

        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')

        log_message(f"Restarting instances with audio device '{self.audio_device}'...", self.config)

        # Kill mpv karaoke — use IPC quit first (handles reconnected instances
        # where we have IPC access but no Popen handle), then fall back to process kill
        self._send_ipc(["quit"])
        time.sleep(0.5)
        proc = self.processes.get("karaoke")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_message("Terminated mpv karaoke instance.", self.config)
        self.processes["karaoke"] = None
        try:
            os.unlink(self.ipc_socket_path)
        except FileNotFoundError:
            pass

        # Kill VLC filler
        proc = self.processes.get("filler")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_message("Terminated VLC filler instance.", self.config)
        else:
            self._kill_port(filler_port)
            log_message(f"Killed orphan VLC on port {filler_port} for filler.", self.config)
        self.processes["filler"] = None

        self.karaoke_active = False
        self.current_playing_path = None
        self.pitch_semitones = 0
        time.sleep(1)

        # Relaunch both
        self._launch_mpv_karaoke()
        filler_dir = self.config.get('filler_music_dir', '')
        filler_path = os.path.join(filler_dir, self.current_filler_track) if filler_dir and self.current_filler_track else ''
        self._launch_vlc_filler(filler_port, filler_pw, filler_path, True)
        time.sleep(3)
        self.fade_in_filler()
        self._save_state()
        log_message("Instances restarted successfully.", self.config)

    # ── Playback ───────────────────────────────────────────────────────

    def play_video(self, file_path, display_path=None, overlay_manager=None):
        """Plays a video on the mpv karaoke instance (stop filler, load, play)."""
        if not os.path.exists(file_path):
            log_message(f"ERROR: File not found: {file_path}", self.config)
            return

        if not self.enabled:
            log_message(f"Playback disabled - cannot play {os.path.basename(file_path)}", self.config)
            return

        with self._play_lock:
            self.last_play_time = time.time()
            self.audio_error = False

            # Reset pitch for new song
            self.pitch_semitones = 0

            if display_path is not None:
                self.current_playing_path = display_path
            if overlay_manager is not None:
                overlay_manager.set_karaoke_playing(True)

            # Fade out and stop filler music (releases ALSA device)
            filler_port = self.config.get('filler_vlc_port', 8081)
            filler_pw = self.config.get('filler_vlc_password', 'filler')
            filler_status = self.send_command(filler_port, filler_pw, "")
            if not filler_status or filler_status.get('state') != 'stopped':
                self.fade_out_filler()
                time.sleep(0.3)
                self.ensure_filler_stopped()

            # Load file in mpv (replaces any current playback)
            resp = self._send_ipc(["loadfile", file_path, "replace"])
            if resp is None or resp.get("error") != "success":
                log_message(f"ERROR: mpv failed to load {file_path}", self.config)
                self.audio_error = True
                # Rollback: restore overlay and filler state
                if overlay_manager is not None:
                    overlay_manager.set_karaoke_playing(False)
                self.current_playing_path = None
                self.fade_in_filler()
                return

            # Wait for playback to actually start before setting volume/pitch
            time.sleep(0.5)

            # Set volume (convert from VLC scale)
            mpv_vol = self._vlc_to_mpv_volume(self.karaoke_volume)
            self._set_property("volume", mpv_vol)

            self.karaoke_active = True
            self._save_state()
            log_message(f"Playback started for {os.path.basename(file_path)}.", self.config)

        # Verify audio is working after a brief delay (outside lock)
        def verify_playback():
            time.sleep(3)
            if not self.karaoke_active:
                return
            time_pos = self._get_property("time-pos")
            if time_pos is not None and time_pos > 0:
                self.audio_error = False
            else:
                log_message("WARNING: mpv not progressing - possible audio device issue", self.config)
                self.audio_error = True
        threading.Thread(target=verify_playback, daemon=True).start()

    # ── Monitor thread ─────────────────────────────────────────────────

    def monitor_karaoke(self):
        """Background thread: monitors mpv for end-of-file events via IPC.

        Uses a persistent socket connection to receive events. Falls back to
        polling if the persistent connection can't be established.
        """
        while True:
            try:
                self._monitor_via_events()
            except Exception as e:
                log_message(f"mpv monitor event loop error: {e}, falling back to polling", self.config)
                self._monitor_via_polling()

    def _monitor_via_events(self):
        """Listen for mpv IPC events on a persistent socket connection."""
        while True:
            # Wait for IPC socket to exist
            if not os.path.exists(self.ipc_socket_path):
                time.sleep(2)
                continue

            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect(self.ipc_socket_path)
                s.settimeout(None)  # Blocking reads for event stream
            except OSError:
                time.sleep(2)
                continue

            log_message("mpv monitor connected to IPC socket.", self.config)
            buf = b""
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break  # Socket closed
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
        """Fallback: poll mpv status every 2 seconds (like VLCManager)."""
        while True:
            time.sleep(2)
            if not self.karaoke_active:
                continue

            if time.time() - self.last_seek_time < 5:
                continue
            if time.time() - self.last_play_time < 5:
                continue

            idle = self._get_property("idle-active")
            if idle is True:
                self._handle_karaoke_ended()

    def _handle_karaoke_ended(self):
        """Handle the end of a karaoke song."""
        if not self.karaoke_active:
            return
        log_message("Karaoke video finished playing.", self.config)
        self.karaoke_active = False
        self.current_playing_path = None
        self.pitch_semitones = 0
        self._save_state()
        if self.on_karaoke_end:
            try:
                self.on_karaoke_end()
            except Exception:
                pass
        self.fade_in_filler()
