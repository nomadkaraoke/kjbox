"""VLCManager class: launch, control, and manage dual VLC instances."""

import json
import os
import signal
import subprocess
import threading
import time

import requests

from config import APP_DIR, is_pi
from utils import log_message

STATE_FILE = '/tmp/kj-vlc-state.json'


class VLCManager:
    """Manages dual VLC instances (karaoke + filler) for audio/video playback."""

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
        self._play_lock = threading.Lock()
        self._fade_cancel = threading.Event()
        self.on_karaoke_end = None  # Optional callback when karaoke ends

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
            # fuser not available, try lsof
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

    def try_reconnect(self):
        """Attempt to reconnect to existing VLC instances after a restart.

        Returns a dict with 'karaoke' and 'filler' keys indicating which
        instances were found running.
        """
        if not self.enabled:
            return {'karaoke': False, 'filler': False}

        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')

        found = {'karaoke': False, 'filler': False}
        saved = self._load_state()

        # Check karaoke VLC
        karaoke_status = self._probe_vlc(karaoke_port, karaoke_pw)
        if karaoke_status:
            found['karaoke'] = True
            state = karaoke_status.get('state', 'stopped')
            log_message(f"Reconnected to existing karaoke VLC (state: {state}).", self.config)
            if state in ('playing', 'paused'):
                self.karaoke_active = True
                self.current_playing_path = saved.get('current_playing_path')
                self.last_play_time = time.time()
                log_message(f"Recovered karaoke playback: {self.current_playing_path}", self.config)

        # Check filler VLC
        filler_status = self._probe_vlc(filler_port, filler_pw)
        if filler_status:
            found['filler'] = True
            self.current_filler_track = saved.get('current_filler_track', self.current_filler_track)
            log_message(f"Reconnected to existing filler VLC (state: {filler_status.get('state')}).", self.config)

        return found

    def launch_instance(self, name, port, password, media_file=None, loop=False):
        """Launches a VLC instance with the HTTP interface enabled."""
        if not self.enabled:
            return

        if self.processes[name] and self.processes[name].poll() is None:
            log_message(f"VLC instance '{name}' is already running.", self.config)
            return

        log_message(f"Launching VLC instance '{name}' on port {port} with audio device '{self.audio_device}'...", self.config)
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
        if name == 'karaoke':
            command.append('--fullscreen')

        if media_file:
            command.append(media_file)
        if loop:
            command.extend(['--loop'])

        # On Pi: VLC refuses to run as root; wrap with sudo -u dietpi and set display env
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
            vlc_log = open(os.path.join(log_dir, f'vlc-{name}.log'), 'a')
            vlc_log.write(f"\n--- VLC '{name}' starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            vlc_log.flush()
            process = subprocess.Popen(full_command, stdout=vlc_log, stderr=vlc_log,
                                       start_new_session=True)
            self.processes[name] = process
            log_message(f"VLC instance '{name}' launched with PID {process.pid}.", self.config)
            time.sleep(2)
        except FileNotFoundError:
            log_message(f"VLC not found - '{name}' instance not launched.", self.config)
            if vlc_log:
                vlc_log.close()

    def send_command(self, port, password, command, is_path=False, debug=False):
        """Sends a command to a VLC HTTP interface."""
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

        if debug:
            log_message(f"DEBUG: Sending VLC command to {url}", self.config)

        try:
            s = requests.Session()
            s.auth = ('', password)
            response = s.get(url, timeout=5)
            response.raise_for_status()
            response_json = response.json()
            if debug:
                log_message(f"DEBUG: VLC response status: {response.status_code}", self.config)
                log_message(f"DEBUG: VLC response body: {response_json}", self.config)
            return response_json
        except requests.exceptions.RequestException as e:
            if debug:
                log_message(f"Error sending command to VLC on port {port}: {e}", self.config)
            return None
        except Exception as e:
            log_message(f"An unexpected error occurred when calling VLC: {e}", self.config)
            return None

    def fade_music(self, port, password, start_vol, end_vol, duration_s=1.5, cancel_event=None):
        """Gradually fades volume over a set duration. Stops early if cancel_event is set."""
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
        # Cancel any in-progress fade before starting a new one
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
        # Cancel any in-progress fade before starting a new one
        self._fade_cancel.set()
        self._fade_cancel = threading.Event()
        log_message("Fading out filler music...", self.config)
        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')
        # Read actual current volume instead of assuming filler_volume
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

    def ensure_karaoke_released(self):
        """Stops karaoke VLC and verifies it has released the audio device."""
        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        self.send_command(karaoke_port, karaoke_pw, "pl_stop")
        self.send_command(karaoke_port, karaoke_pw, "pl_empty")
        for attempt in range(5):
            status = self.send_command(karaoke_port, karaoke_pw, "")
            if status and status.get('state') == 'stopped':
                return True
            self.send_command(karaoke_port, karaoke_pw, "pl_stop")
            time.sleep(0.5)
        log_message("WARNING: Could not confirm karaoke VLC released audio device after 5 attempts", self.config)
        return False

    def play_video(self, file_path, display_path=None, overlay_manager=None):
        """Plays a video on the karaoke VLC instance (stop filler, load, play).

        Args:
            file_path: Path to the actual media file to play (may be extracted MP3).
            display_path: Path to show in status/UI (original file the user selected).
                         If None, not set (caller manages current_playing_path).
            overlay_manager: If provided, set karaoke_playing(True) inside the lock.
        """
        if not os.path.exists(file_path):
            log_message(f"ERROR: File not found: {file_path}", self.config)
            return

        if not self.enabled:
            log_message(f"VLC disabled - cannot play {os.path.basename(file_path)}", self.config)
            return

        with self._play_lock:
            self.last_play_time = time.time()
            karaoke_port = self.config.get('karaoke_vlc_port', 8080)
            karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')

            self.audio_error = False

            # Set display state inside the lock so it's atomic with playback start
            if display_path is not None:
                self.current_playing_path = display_path
            if overlay_manager is not None:
                overlay_manager.set_karaoke_playing(True)

            # Fade out and stop filler music (releases audio device)
            # Skip the 3s fade if filler is already stopped (e.g. switching songs)
            filler_port = self.config.get('filler_vlc_port', 8081)
            filler_pw = self.config.get('filler_vlc_password', 'filler')
            filler_status = self.send_command(filler_port, filler_pw, "")
            if not filler_status or filler_status.get('state') != 'stopped':
                self.fade_out_filler()
                time.sleep(0.3)
                self.ensure_filler_stopped()

            # Load and play the video
            self.send_command(karaoke_port, karaoke_pw, "pl_empty")
            time.sleep(0.1)
            self.send_command(karaoke_port, karaoke_pw, f"in_enqueue&input={file_path}", is_path=True)
            time.sleep(0.1)
            self.send_command(karaoke_port, karaoke_pw, f"volume&val={self.karaoke_volume}")
            time.sleep(0.1)
            self.send_command(karaoke_port, karaoke_pw, "pl_play")

            self.karaoke_active = True
            self._save_state()
            log_message(f"Playback started for {os.path.basename(file_path)}.", self.config)

        # Verify audio is actually working after a brief delay (outside lock)
        def verify_playback():
            time.sleep(3)
            status = self.send_command(karaoke_port, karaoke_pw, "")
            if status and status.get('state') == 'playing':
                self.audio_error = False
            elif self.karaoke_active:
                log_message("WARNING: Karaoke VLC not in 'playing' state - possible audio device issue", self.config)
                self.audio_error = True
        threading.Thread(target=verify_playback, daemon=True).start()

    def restart_instances(self):
        """Terminates both VLC processes and relaunches them with the current audio device."""
        if not self.enabled:
            return

        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        filler_port = self.config.get('filler_vlc_port', 8081)
        filler_pw = self.config.get('filler_vlc_password', 'filler')

        log_message(f"Restarting VLC instances with audio device '{self.audio_device}'...", self.config)

        # Terminate existing VLC processes (owned or orphan)
        for name in list(self.processes.keys()):
            proc = self.processes[name]
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log_message(f"Terminated VLC instance '{name}'.", self.config)
            else:
                # No owned process — kill by port (orphan from previous run)
                port = self.config.get(f'{name}_vlc_port', 8080 if name == 'karaoke' else 8081)
                self._kill_port(port)
                log_message(f"Killed orphan VLC on port {port} for '{name}'.", self.config)
            self.processes[name] = None

        self.karaoke_active = False
        self.current_playing_path = None
        time.sleep(1)

        # Relaunch both instances
        self.launch_instance("karaoke", karaoke_port, karaoke_pw)
        filler_dir = self.config.get('filler_music_dir', '')
        filler_path = os.path.join(filler_dir, self.current_filler_track) if filler_dir and self.current_filler_track else ''
        self.launch_instance("filler", filler_port, filler_pw, filler_path, True)
        time.sleep(3)
        self.fade_in_filler()
        self._save_state()
        log_message("VLC instances restarted successfully.", self.config)

    def monitor_karaoke(self):
        """Background thread loop: checks if a karaoke song has ended."""
        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        while True:
            time.sleep(2)
            if not self.karaoke_active:
                continue

            # Skip check briefly after a seek - VLC may report 'stopped' transiently
            if time.time() - self.last_seek_time < 5:
                continue

            # Skip check briefly after a play request - VLC may report 'stopped' transiently
            if time.time() - self.last_play_time < 5:
                continue

            status = self.send_command(karaoke_port, karaoke_pw, "", debug=False)
            if not status:
                continue

            if status.get('state') == 'stopped':
                log_message("Karaoke video finished playing.", self.config)
                self.karaoke_active = False
                self.current_playing_path = None
                self._save_state()
                if self.on_karaoke_end:
                    try:
                        self.on_karaoke_end()
                    except Exception:
                        pass
                self.ensure_karaoke_released()
                self.fade_in_filler()

    # ── API-compatibility shims (MpvManager-style named methods) ────────
    # Routes.py calls these named methods; delegate to HTTP send_command.

    pitch_semitones = 0  # VLC has no pitch control; attribute kept for API compat

    def seek_karaoke(self, seconds):
        """Seek karaoke to an absolute position in seconds."""
        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        self.last_seek_time = time.time()
        self.send_command(karaoke_port, karaoke_pw, f"seek&val={int(seconds)}")

    def pause_resume_karaoke(self):
        """Toggle pause. Returns True if now paused, False if now playing, None on error."""
        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        self.send_command(karaoke_port, karaoke_pw, "pl_pause")
        time.sleep(0.5)
        status = self.send_command(karaoke_port, karaoke_pw, "")
        if not status:
            return None
        return status.get('state') == 'paused'

    def stop_karaoke(self):
        """Stop karaoke playback and clear state."""
        self.ensure_karaoke_released()
        self.karaoke_active = False
        self.current_playing_path = None
        self.audio_error = False
        self._save_state()

    def set_karaoke_volume_live(self, vlc_level):
        """Set karaoke volume live (VLC-scale 0-512)."""
        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        self.karaoke_volume = vlc_level
        self.send_command(karaoke_port, karaoke_pw, f"volume&val={int(vlc_level)}")

    def get_karaoke_status(self):
        """Return {state, time, length} for the karaoke VLC instance."""
        karaoke_port = self.config.get('karaoke_vlc_port', 8080)
        karaoke_pw = self.config.get('karaoke_vlc_password', 'karaoke')
        status = self.send_command(karaoke_port, karaoke_pw, "")
        if not status:
            return {"state": "stopped", "time": 0, "length": 0}
        return {
            "state": status.get('state', 'stopped'),
            "time": int(status.get('time', 0) or 0),
            "length": int(status.get('length', 0) or 0),
        }

    def set_pitch(self, semitones):
        """No-op: VLC playback path has no real-time pitch shifting."""
        log_message(
            f"set_pitch({semitones}) ignored — VLC karaoke path has no rubberband filter.",
            self.config,
        )
