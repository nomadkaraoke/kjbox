"""VlcKaraokePlayer: dual-VLC karaoke backend (renders CDG natively).

Implements the `KaraokePlayer` protocol. The filler VLC is owned by a
shared `FillerVLC` instance (see `filler.py`) that this player does NOT
manage — the coordinator wires it in.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

import requests

from config import APP_DIR, is_pi
from filler import FillerVLC
from karaoke_player import fade_steps
from utils import log_message

STATE_FILE = '/tmp/kj-vlc-state.json'


class VlcKaraokePlayer:
    """Karaoke backend that uses a dedicated cvlc on port 8080."""

    name = 'vlc'
    supports_pitch = False
    supports_cdg = True

    def __init__(self, config, filler: FillerVLC, enabled=None):
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
        self._play_lock = threading.Lock()
        self.on_karaoke_end = None
        # Fired once (with {engine, returncode, song}) when the VLC process is
        # found to have exited unexpectedly, so the coordinator can auto-restart
        # and notify the operator. Mirrors MpvKaraokePlayer. Reset on launch().
        self.on_engine_died = None
        self._death_notified = False
        self._socket_dead_count = 0   # debounce for reconnected-VLC liveness checks
        self._last_file_path = None  # actual path of the last play() — for crash Retry
        self._monitor_stop = threading.Event()

    # Protocol conformance: VLC has no real-time pitch shifting
    @property
    def volume(self) -> int:
        return self.karaoke_volume

    @property
    def pitch_semitones(self) -> int:
        return 0

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

    # ── HTTP helpers for karaoke VLC ────────────────────────────────────

    @property
    def _port(self) -> int:
        return self.config.get('karaoke_vlc_port', 8080)

    @property
    def _password(self) -> str:
        return self.config.get('karaoke_vlc_password', 'karaoke')

    def _probe(self):
        try:
            s = requests.Session()
            s.auth = ('', self._password)
            resp = s.get(f"http://localhost:{self._port}/requests/status.json", timeout=2)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _send(self, command, is_path=False, debug=False):
        if not self.enabled:
            return None

        if '&' in command and not is_path:
            url = f"http://localhost:{self._port}/requests/status.json?command={command}"
        else:
            parts = command.split('&input=', 1)
            cmd_part = parts[0]
            input_part = parts[1] if len(parts) > 1 else ''
            encoded_input = requests.utils.quote(input_part)
            url = f"http://localhost:{self._port}/requests/status.json?command={cmd_part}&input={encoded_input}"

        try:
            s = requests.Session()
            s.auth = ('', self._password)
            response = s.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if debug:
                log_message(f"Error sending command to karaoke VLC: {e}", self.config)
            return None
        except Exception as e:
            log_message(f"Unexpected error calling karaoke VLC: {e}", self.config)
            return None

    # ── Video window geometry ──────────────────────────────────────────

    @staticmethod
    def _video_window_args(margin_px, strip_vlc):
        """Launch flags controlling the karaoke video window.

        Returns ``[]`` (windowed — positioned afterwards by ``wmctrl``) only when
        a top strip is reserved (``margin_px > 0``) AND the VLC strip is enabled
        (``video_strip_vlc``). Otherwise ``['--fullscreen']`` — the safe default,
        since VLC's own geometry flags are unreliable and window repositioning
        needs on-device validation.
        """
        if margin_px and margin_px > 0 and strip_vlc:
            return []
        return ['--fullscreen']

    def _reposition_window(self, margin_px, screen_w, screen_h):
        """Best-effort: move the karaoke VLC window below the reserved strip.

        VLC's geometry CLI flags are unreliable, so we reposition the mapped
        window with ``wmctrl`` after it appears. FIDDLY / device-validated: the
        filler VLC is a second "VLC media player" window, so ``wmctrl -r``
        matching is ambiguous — gate this behind ``video_strip_vlc`` and confirm
        on the device before trusting it. Never raises; logs and leaves VLC
        fullscreen on any failure.
        """
        title = 'VLC media player'
        video_h = max(1, screen_h - margin_px)
        cmds = [
            ['wmctrl', '-r', title, '-b', 'remove,fullscreen'],
            ['wmctrl', '-r', title, '-e', f'0,0,{margin_px},{screen_w},{video_h}'],
        ]
        if is_pi():
            wrapper = ['sudo', '-u', 'dietpi', 'env', 'DISPLAY=:0',
                       'XDG_RUNTIME_DIR=/run/user/1000']
            cmds = [wrapper + c for c in cmds]
        for c in cmds:
            try:
                r = subprocess.run(c, capture_output=True, timeout=5, check=False)
            except (OSError, subprocess.SubprocessError) as e:
                log_message(f"wmctrl reposition failed ({' '.join(c[-4:])}): {e}", self.config)
                return
            if r.returncode != 0:
                # Non-zero usually means the window title didn't match (e.g. VLC
                # not mapped yet). Don't claim success.
                log_message(
                    f"wmctrl reposition non-zero exit ({' '.join(c[-4:])}): "
                    f"{r.stderr.decode(errors='replace').strip()}", self.config)
                return
        log_message(
            f"Positioned karaoke VLC below {margin_px}px top strip.", self.config)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def launch(self):
        """Launch the karaoke VLC instance."""
        if not self.enabled:
            return

        if self.process and self.process.poll() is None:
            log_message("Karaoke VLC already running.", self.config)
            return

        log_message(
            f"Launching karaoke VLC on port {self._port} with audio device '{self.audio_device}'...",
            self.config,
        )
        margin_px = int(self.config.get('video_top_margin_px', 0) or 0)
        video_args = self._video_window_args(
            margin_px, bool(self.config.get('video_strip_vlc', False)))
        command = [
            'cvlc',
            '--extraintf', 'http',
            '--http-host', '0.0.0.0',
            '--http-port', str(self._port),
            '--http-password', self._password,
            '--no-video-title-show',
            '--aout', 'alsa',
            '--alsa-audio-device', self.audio_device,
            *video_args,
        ]

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
            vlc_log = open(os.path.join(log_dir, 'vlc-karaoke.log'), 'a')
            vlc_log.write(f"\n--- karaoke VLC starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            vlc_log.flush()
            process = subprocess.Popen(
                full_command, stdout=vlc_log, stderr=vlc_log, start_new_session=True,
            )
            self.process = process
            self._death_notified = False  # re-arm crash detection for this process
            self._socket_dead_count = 0
            log_message(f"Karaoke VLC launched with PID {process.pid}.", self.config)
            time.sleep(2)
            # Windowed (video_args empty) => reposition below the reserved strip
            # now the VLC window has had time to map.
            if not video_args:
                self._reposition_window(
                    margin_px,
                    int(self.config.get('screen_width', 1920) or 1920),
                    int(self.config.get('screen_height', 1080) or 1080),
                )
        except FileNotFoundError:
            log_message("VLC not found — karaoke instance not launched.", self.config)
            if vlc_log:
                vlc_log.close()

    def try_reconnect(self) -> bool:
        if not self.enabled:
            return False
        status = self._probe()
        if status:
            state = status.get('state', 'stopped')
            log_message(f"Reconnected to existing karaoke VLC (state: {state}).", self.config)
            if state in ('playing', 'paused'):
                saved = self._load_state()
                self.active = True
                self.current_path = saved.get('current_playing_path')
                self.last_play_time = time.time()
                log_message(f"Recovered karaoke playback: {self.current_path}", self.config)
            return True
        return False

    def shutdown(self):
        """Terminate the karaoke VLC process. Used during renderer swap."""
        self._monitor_stop.set()
        self.ensure_released()
        proc = self.process
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_message("Terminated karaoke VLC.", self.config)
        else:
            # Orphan from previous run — kill by port
            self._kill_port(self._port)
            log_message(f"Killed orphan karaoke VLC on port {self._port}.", self.config)
        self.process = None
        self.active = False
        self.current_path = None

    @staticmethod
    def _kill_port(port):
        import signal as _signal
        try:
            result = subprocess.run(
                ['fuser', f'{port}/tcp'], capture_output=True, text=True,
            )
            pids = result.stdout.strip().split()
            for pid in pids:
                try:
                    os.kill(int(pid), _signal.SIGTERM)
                except (ValueError, OSError):
                    pass
            if pids:
                time.sleep(1)
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ['lsof', '-ti', f':{port}'], capture_output=True, text=True,
                )
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        os.kill(int(pid), _signal.SIGTERM)
                    except (ValueError, OSError):
                        pass
                if pids and pids[0]:
                    time.sleep(1)
            except FileNotFoundError:
                pass

    # ── Playback control ───────────────────────────────────────────────

    def play(self, file_path, display_path=None, overlay_manager=None, audio_file=None):
        """Play a karaoke file. Caller (coordinator) must stop filler first.

        ``audio_file`` is accepted for interface parity with the mpv renderer
        but ignored: for CDG zips the caller hands VLC the .mp3 and VLC
        auto-discovers the sibling .cdg, so no external audio track is needed.
        """
        if not os.path.exists(file_path):
            log_message(f"ERROR: File not found: {file_path}", self.config)
            return

        if not self.enabled:
            log_message(f"VLC disabled — cannot play {os.path.basename(file_path)}", self.config)
            return

        with self._play_lock:
            self.last_play_time = time.time()
            self._last_file_path = file_path  # remembered for crash-Retry
            self.audio_error = False

            if display_path is not None:
                self.current_path = display_path
            if overlay_manager is not None:
                overlay_manager.set_karaoke_playing(True)

            self._send("pl_empty")
            time.sleep(0.1)
            self._send(f"in_enqueue&input={file_path}", is_path=True)
            time.sleep(0.1)
            self._send(f"volume&val={self.karaoke_volume}")
            time.sleep(0.1)
            self._send("pl_play")

            self.active = True
            self._save_state()
            log_message(f"Playback started for {os.path.basename(file_path)}.", self.config)

        def verify():
            time.sleep(3)
            status = self._probe()
            if status and status.get('state') == 'playing':
                self.audio_error = False
            elif self.active:
                log_message(
                    "WARNING: Karaoke VLC not in 'playing' state — possible audio device issue",
                    self.config,
                )
                self.audio_error = True

        threading.Thread(target=verify, daemon=True).start()

    def stop(self):
        self.ensure_released()
        self.active = False
        self.current_path = None
        self.audio_error = False
        self._save_state()

    def seek(self, seconds):
        self.last_seek_time = time.time()
        self._send(f"seek&val={int(seconds)}")

    def pause_resume(self):
        self._send("pl_pause")
        time.sleep(0.5)
        status = self._send("")
        if not status:
            return None
        return status.get('state') == 'paused'

    def set_volume(self, vlc_level):
        self.karaoke_volume = int(vlc_level)
        self._send(f"volume&val={int(vlc_level)}")

    def set_pitch(self, semitones):
        log_message(
            f"set_pitch({semitones}) ignored — VLC karaoke path has no rubberband filter.",
            self.config,
        )

    def fadeout(self, duration_s=3.0):
        """Fade karaoke volume to 0 over duration_s, stop, restore volume."""
        saved_volume = self.karaoke_volume
        steps = fade_steps(duration_s)
        delay = duration_s / steps

        def _do():
            for i in range(steps + 1):
                vol = int(saved_volume * (1 - i / steps))
                self._send(f"volume&val={vol}")
                time.sleep(delay)
            self.stop()
            # Restore configured volume for next song
            self.karaoke_volume = saved_volume
            self._send(f"volume&val={saved_volume}")
            log_message(f"Fadeout complete, volume restored to {saved_volume}.", self.config)

        threading.Thread(target=_do, daemon=True).start()

    def get_status(self):
        status = self._send("")
        # `active` + a current_path are set on play and cleared only by a real
        # stop/fadeout or the guarded monitor — never by VLC's transient 'stopped'
        # reports — so together they form a stable "a song is loaded" signal.
        song_loaded = self.active and self.current_path is not None

        if not status:
            # HTTP blip (timeout/error). Don't flap the reported state to 'stopped'
            # mid-song — that would flicker the now-playing pill and grey out the
            # playback buttons. The monitor also ignores blips (see monitor()); mirror
            # that here by reporting the last-known playing state while a song is loaded.
            if song_loaded:
                return {"state": "playing", "time": 0, "length": 0}
            return {"state": "stopped", "time": 0, "length": 0}

        raw_state = status.get('state', 'stopped')
        # VLC's HTTP interface reports 'stopped' transiently for a few seconds after a
        # play or seek. The end-of-song monitor guards against this so it doesn't falsely
        # fire on_karaoke_end; mirror that guard here so consumers of get_status (button
        # gating, now-playing pill) see mpv-equivalent, non-flickering state.
        if raw_state == 'stopped' and song_loaded:
            now = time.time()
            within_guard = (now - self.last_play_time < 5) or (now - self.last_seek_time < 5)
            if within_guard:
                raw_state = 'playing'

        return {
            "state": raw_state,
            "time": int(status.get('time', 0) or 0),
            "length": int(status.get('length', 0) or 0),
        }

    def get_perf(self):
        """Perf snapshot for the monitor (idle-shaped dict when not playing).

        VLC exposes cumulative displayed/lost picture counts via its HTTP status
        interface; the sampler derives displayed-fps and a per-second lost-frame
        rate from the deltas. Never raises.
        """
        if not self.active:
            return {"playing": False}
        raw = self._send("") or {}
        st = raw.get("stats", {}) or {}
        return {
            "playing": True,
            "render_fps": None,          # derived by the sampler from displayed delta
            "container_fps": None,
            "display_fps": None,
            "hwdec": None,               # legacy path software-decodes
            "codec": None,
            "width": None,
            "height": None,
            "vo_drops": None,
            "decoder_drops": None,
            "delayed": None,
            "vlc_displayed": st.get("displayedpictures"),
            "vlc_lost": st.get("lostpictures"),
        }

    def ensure_released(self):
        """Stop karaoke VLC and verify it returns to state='stopped'."""
        self._send("pl_stop")
        self._send("pl_empty")
        for _ in range(5):
            status = self._send("")
            if status and status.get('state') == 'stopped':
                return True
            self._send("pl_stop")
            time.sleep(0.5)
        log_message("WARNING: Could not confirm karaoke VLC released audio device.", self.config)
        return False

    # ── Monitor ────────────────────────────────────────────────────────

    def _notify_if_dead(self):
        """Detect an unexpected VLC exit (crash) and fire on_engine_died once.

        Uses the Popen exit code as the unambiguous signal: `poll()` is None
        while alive, an int once the process has exited. A death during an
        intentional shutdown/restart (`_monitor_stop` set) is expected and
        ignored, so this only fires for genuine crashes — not a normal song-end
        (a live HTTP 'stopped' response) or a transient HTTP blip.

        Returns True if a fresh death was detected (the caller should stop
        monitoring — recovery will start a new player + monitor).
        """
        if self._monitor_stop.is_set() or self._death_notified:
            return False
        proc = self.process
        if proc is not None:
            if proc.poll() is None:
                self._socket_dead_count = 0
                return False
            code = proc.returncode            # spawned process exited → dead
        else:
            # Reconnected (no Popen handle, e.g. VLC survived a service
            # restart): use the HTTP probe, debounced so a transient HTTP blip
            # during a restart isn't mistaken for a crash.
            if self._probe() is not None:
                self._socket_dead_count = 0
                return False
            self._socket_dead_count += 1
            if self._socket_dead_count < 2:
                return False
            code = None
        self._death_notified = True
        # The engine is gone — it isn't playing anything. Clear playing-state so
        # /status doesn't report a stale song during recovery, and only offer a
        # Retry song if it actually died mid-playback (not an idle crash).
        was_active = self.active
        self.active = False
        song = self.current_path if was_active else None
        file_path = self._last_file_path if was_active else None
        log_message(
            f"VLC engine died unexpectedly (exit {code}) — signalling recovery.",
            self.config,
        )
        cb = self.on_engine_died
        if cb:
            try:
                cb({'engine': self.name, 'returncode': code,
                    'song': song, 'file_path': file_path})
            except Exception as e:
                log_message(f"on_engine_died callback error: {e}", self.config)
        return True

    def monitor(self):
        """Poll karaoke VLC every 2s; fire on_karaoke_end when it stops."""
        while not self._monitor_stop.is_set():
            time.sleep(2)
            if self._monitor_stop.is_set():
                return
            if self._notify_if_dead():
                return
            if not self.active:
                continue

            # Skip briefly after seek/play — VLC reports stopped transiently
            if time.time() - self.last_seek_time < 5:
                continue
            if time.time() - self.last_play_time < 5:
                continue

            status = self._send("")
            if not status:
                continue

            if status.get('state') == 'stopped':
                log_message("Karaoke video finished playing.", self.config)
                self.active = False
                self.current_path = None
                self._save_state()
                self.ensure_released()
                if self.on_karaoke_end:
                    try:
                        self.on_karaoke_end()
                    except Exception:
                        pass
