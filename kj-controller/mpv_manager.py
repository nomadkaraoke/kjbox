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
from karaoke_player import fade_steps
from utils import log_message

STATE_FILE = '/tmp/kj-mpv-state.json'
MPV_SOCKET_PATH = '/tmp/mpv-karaoke.sock'
PITCH_MIN = -6
PITCH_MAX = 6

# Playback-progress verification. A cold 4K file (or slow decode init) can
# leave time-pos at 0 for a few seconds even though audio is fine, so a single
# 3s sample produced false "audio device issue" alarms mid-show. Poll instead:
# succeed the instant time-pos advances, and only flag a problem if it is STILL
# stuck after the full window.
PLAYBACK_VERIFY_TIMEOUT = 10.0   # seconds to wait for time-pos to advance
PLAYBACK_VERIFY_INTERVAL = 0.5   # poll cadence


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
        # Fired once (with {engine, returncode, song}) when the mpv process is
        # found to have exited unexpectedly, so the coordinator can auto-restart
        # and notify the operator. Reset on each launch().
        self.on_engine_died = None
        self._death_notified = False
        self._socket_dead_count = 0   # debounce for reconnected-mpv liveness checks
        self._last_file_path = None  # actual path of the last play() — for crash Retry
        self._pitch_semitones = 0
        # Original-vocals guide: an isolated-vocals track mixed UNDER the karaoke
        # audio via --lavfi-complex amix, so the existing @rb rubberband (in --af)
        # pitches the combined output. Default 0 = OFF. See docs/AUDIO.md.
        self._vocals_file = None      # resolved guide path for the current song
        self._vocals_volume = 0       # vlc scale 0-256 (0 = off); guide gain = /256
        self._lavfi_active = False    # is a lavfi-complex mix currently set in mpv
        self.ipc_socket_path = MPV_SOCKET_PATH
        self._ipc_lock = threading.Lock()
        self._ipc_request_id = 0
        self._monitor_stop = threading.Event()

    # Protocol surface
    @property
    def volume(self) -> int:
        return self.karaoke_volume

    @property
    def pitch_semitones(self) -> int:
        return self._pitch_semitones

    @property
    def vocals_volume(self) -> int:
        return self._vocals_volume

    @property
    def has_vocals_track(self) -> bool:
        """True when an original-vocals guide is mixed into the current song."""
        return self._vocals_file is not None

    # ── IPC ────────────────────────────────────────────────────────────

    def _send_ipc(self, command):
        """Send a JSON command to mpv's IPC socket and return the parsed reply.

        mpv broadcasts asynchronous event lines (playback-restart, property
        changes, end-file, …) on every client connection, interleaved with
        command replies. We tag each command with a unique request_id and keep
        reading whole lines until the reply carrying THAT id arrives, skipping
        events. The old "read until the first newline" logic could return an
        event by mistake, which surfaced as a spurious None — and the progress
        check misread that as a stalled/silent player. Bounded by the 5s socket
        timeout; returns None on any socket error or timeout.
        """
        with self._ipc_lock:
            self._ipc_request_id += 1
            req_id = self._ipc_request_id
            s = None
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect(self.ipc_socket_path)
                msg = json.dumps({"command": command, "request_id": req_id}) + "\n"
                s.sendall(msg.encode())
                buf = b""
                while True:
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            parsed = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # skip a malformed line, keep reading
                        if parsed.get("request_id") == req_id:
                            return parsed
                        # else: async event or stale reply — ignore, read on
                    chunk = s.recv(4096)
                    if not chunk:
                        return None  # socket closed before our reply arrived
                    buf += chunk
            except OSError:
                # socket.timeout is an OSError subclass — treat as no reply
                return None
            finally:
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

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

    # ── Video window geometry ──────────────────────────────────────────

    @staticmethod
    def _video_window_args(margin_px, screen_w, screen_h):
        """mpv args placing the karaoke video BELOW a reserved top strip.

        ``margin_px > 0`` reserves a top strip of that height for the scrolling
        ticker and renders the video in a borderless window filling the area
        beneath it, so the ticker no longer composites over the changing 4K
        frame (the measured 4K frame-drop lever). ``margin_px <= 0`` keeps the
        old fullscreen behaviour — a clean rollback path. Validated on device:
        ``mpv --no-border --geometry=1920x1000+0+80`` keeps hwdec=vaapi with 0
        drops (see docs/archive/2026-07-07-perf-layout-fix-plan.md).
        """
        if margin_px and margin_px > 0:
            video_h = max(1, screen_h - margin_px)
            return ['--no-border', f'--geometry={screen_w}x{video_h}+0+{margin_px}']
        return ['--fs']

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

        # Fullscreen, or a borderless window below the reserved ticker strip.
        video_args = self._video_window_args(
            int(self.config.get('video_top_margin_px', 0) or 0),
            int(self.config.get('screen_width', 1920) or 1920),
            int(self.config.get('screen_height', 1080) or 1080),
        )

        if self.audio_backend == 'pipewire':
            log_message("Launching mpv karaoke with PulseAudio/PipeWire audio...", self.config)
            command = [
                'mpv', '--idle',
                *video_args,
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
                *video_args,
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
            self._death_notified = False  # re-arm crash detection for this process
            self._socket_dead_count = 0
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

    def play(self, file_path, display_path=None, overlay_manager=None, audio_file=None,
             vocals_file=None):
        if not os.path.exists(file_path):
            log_message(f"ERROR: File not found: {file_path}", self.config)
            return

        if not self.enabled:
            log_message(f"mpv disabled — cannot play {os.path.basename(file_path)}", self.config)
            return

        with self._play_lock:
            self.last_play_time = time.time()
            self._last_file_path = file_path  # remembered for crash-Retry
            self.audio_error = False
            self._pitch_semitones = 0  # reset for each new song
            self._vocals_volume = 0    # guide starts OFF each song; KJ raises it live
            self._vocals_file = None

            if display_path is not None:
                self.current_path = display_path
            if overlay_manager is not None:
                overlay_manager.set_karaoke_playing(True)

            # If the previous song had an original-vocals mix, clear it BEFORE
            # loading the next file. Clearing while the old graph's aid2 still
            # exists keeps the @rb audio filter healthy; clearing AFTER loadfile
            # (aid2 gone) breaks af-command rb -> pitch (verified on mpv 0.37).
            # Only touched when a guide was active, so normal->normal playback is
            # unchanged.
            if self._lavfi_active:
                self._send_ipc(["set_property", "lavfi-complex", ""])
                self._lavfi_active = False

            resp = self._send_ipc(["loadfile", file_path, "replace"])
            if resp is None or resp.get("error") != "success":
                log_message(f"ERROR: mpv failed to load {file_path}", self.config)
                self.audio_error = True
                if overlay_manager is not None:
                    overlay_manager.set_karaoke_playing(False)
                self.current_path = None
                return

            # For a CDG zip, file_path is the .cdg (graphics); attach the matching
            # .mp3 as an external audio track. `audio-add` is used rather than a
            # loadfile option because the loadfile options-arg position differs
            # between mpv 0.37 and 0.38+, whereas `audio-add` is stable across
            # versions and needs no value escaping (path is a discrete IPC arg).
            if audio_file:
                aresp = self._send_ipc(["audio-add", audio_file, "select"])
                if aresp is None or aresp.get("error") != "success":
                    # A CDG .cdg has no audio of its own, so without the mp3 this
                    # would play silent video. Abort rather than start a song the
                    # singer can't hear.
                    log_message(
                        f"ERROR: mpv failed to attach audio {audio_file}", self.config)
                    self._send_ipc(["stop"])
                    self.audio_error = True
                    if overlay_manager is not None:
                        overlay_manager.set_karaoke_playing(False)
                    self.current_path = None
                    return
            elif vocals_file and os.path.exists(vocals_file):
                # Original-vocals guide (NOMAD masters): attach the isolated-vocals
                # track WITHOUT selecting it (the video's own audio stays aid1, the
                # guide becomes aid2), then mix them via lavfi-complex. The @rb
                # rubberband in --af pitches the combined [ao] output, so pitch
                # control is unchanged. Best-effort: a failed attach falls back to
                # normal playback (guide simply absent), never aborts the song.
                gresp = self._send_ipc(["audio-add", vocals_file, "auto"])
                if gresp is not None and gresp.get("error") == "success":
                    self._vocals_file = vocals_file
                    self._apply_vocals_mix()
                else:
                    log_message(
                        f"WARNING: could not attach vocals guide "
                        f"{os.path.basename(vocals_file)} — normal playback",
                        self.config)

            time.sleep(0.5)
            mpv_vol = self._vlc_to_mpv_volume(self.karaoke_volume)
            self._set_property("volume", mpv_vol)

            self.active = True
            self._save_state()
            log_message(f"Playback started for {os.path.basename(file_path)}.", self.config)

        threading.Thread(target=self._verify_playback_progress, daemon=True).start()

    def _verify_playback_progress(self):
        """Confirm playback is actually advancing after a play().

        Polls time-pos rather than taking a single 3s sample: a cold 4K file or
        a slow decode init can leave time-pos at 0 for a few seconds even though
        audio is fine, and the one-shot check turned that into false "audio
        device issue" banners mid-show. Succeeds the moment time-pos advances;
        only flags a problem if it is STILL stuck after the full window. Runs in
        a daemon thread; exits quietly if the song was stopped/replaced.
        """
        deadline = time.time() + PLAYBACK_VERIFY_TIMEOUT
        while time.time() < deadline:
            time.sleep(PLAYBACK_VERIFY_INTERVAL)
            if not self.active:
                return
            time_pos = self._get_property("time-pos")
            if time_pos is not None and time_pos > 0:
                self.audio_error = False
                return
        if not self.active:
            return
        log_message(
            f"WARNING: mpv not progressing after {PLAYBACK_VERIFY_TIMEOUT:.0f}s "
            "— possible playback/audio issue",
            self.config,
        )
        self.audio_error = True

    def stop(self):
        self._send_ipc(["stop"])
        self.active = False
        self.current_path = None
        self.audio_error = False
        self._vocals_file = None
        self._vocals_volume = 0
        self._save_state()

    def set_vocals_volume(self, vlc_level):
        """Set the original-vocals guide level (vlc scale 0-256; 0 = off).
        Rebuilds the lavfi-complex mix live when a guide is active; otherwise
        just remembers the level for the next guide song."""
        self._vocals_volume = max(0, min(256, int(vlc_level)))
        if self.active and self._vocals_file:
            self._apply_vocals_mix()

    def _apply_vocals_mix(self):
        """(Re)build the lavfi-complex graph mixing the guide (aid2) under the
        karaoke audio (aid1). Guide gain 0..1 from _vocals_volume/256. Rubberband
        is NOT in the graph — the global @rb filter in --af pitches the mixed
        [ao] output, so pitch stays shared and its control path is unchanged."""
        if not self._vocals_file:
            return
        gain = max(0.0, min(1.0, self._vocals_volume / 256.0))
        graph = (f"[aid2]volume={gain:.4f}[gv];"
                 f"[aid1][gv]amix=inputs=2:normalize=0[ao]")
        self._set_property("lavfi-complex", graph)
        self._lavfi_active = True

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
        steps = fade_steps(duration_s)
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

    # Properties the perf monitor reads each tick (fetched in ONE round-trip).
    _PERF_PROPS = (
        "estimated-vf-fps", "container-fps", "estimated-display-fps",
        "frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count",
        "hwdec-current", "video-codec", "width", "height",
    )

    def _get_properties(self, names):
        """Fetch several properties over a SINGLE socket round-trip.

        Returns {name: value_or_None}. This is the cheap path for the 1 Hz perf
        sampler: one connect + one lock acquisition for the whole set, instead of
        N separate `_get_property` calls each taking `_ipc_lock`. Never raises.
        """
        out = {n: None for n in names}
        # Reserve a contiguous block of request ids UNDER the lock, then do all
        # socket I/O OUTSIDE it. Holding _ipc_lock across the recv loop (up to the
        # 3s timeout) would block real-time playback commands — seek/pause/volume/
        # pitch — that share the same lock, defeating the point of the monitor.
        # Each call opens its own mpv client socket, so replies never interleave
        # across callers; only the shared _ipc_request_id needs protection.
        with self._ipc_lock:
            base = self._ipc_request_id
            self._ipc_request_id += len(names)
        id_to_name = {base + 1 + i: n for i, n in enumerate(names)}
        s = None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(self.ipc_socket_path)
            for rid, n in id_to_name.items():
                s.sendall((json.dumps(
                    {"command": ["get_property", n], "request_id": rid}) + "\n").encode())
            pending = set(id_to_name)
            buf = b""
            deadline = time.time() + 3
            while pending and time.time() < deadline:
                try:
                    chunk = s.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    rid = msg.get("request_id")
                    if rid in id_to_name:
                        if msg.get("error") == "success":
                            out[id_to_name[rid]] = msg.get("data")
                        pending.discard(rid)
        except OSError:
            pass
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        return out

    def get_perf(self):
        """Perf snapshot for the monitor. Idle-shaped dict when nothing plays.

        Never raises — a dead socket just yields None fields.
        """
        if not self.active:
            return {"playing": False}
        p = self._get_properties(self._PERF_PROPS)
        return {
            "playing": True,
            "render_fps": p.get("estimated-vf-fps"),
            "container_fps": p.get("container-fps"),
            "display_fps": p.get("estimated-display-fps"),
            "hwdec": p.get("hwdec-current"),
            "codec": p.get("video-codec"),
            "width": p.get("width"),
            "height": p.get("height"),
            "vo_drops": p.get("frame-drop-count"),
            "decoder_drops": p.get("decoder-frame-drop-count"),
            "delayed": p.get("vo-delayed-frame-count"),
            "vlc_displayed": None,
            "vlc_lost": None,
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

    def _notify_if_dead(self):
        """Detect an unexpected mpv exit (crash) and fire on_engine_died once.

        Uses the Popen exit code as the unambiguous signal: `poll()` is None
        while alive, an int once the process has exited (negative = killed by
        that signal, e.g. -11 SIGSEGV). A death during an intentional
        shutdown/restart (`_monitor_stop` set) is expected and ignored, so this
        only fires for genuine crashes — not normal song-end (an event on a
        live socket) or a transient IPC blip (process still alive).

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
            # Reconnected (no Popen handle, e.g. mpv survived a service
            # restart): use socket liveness, debounced so a transient IPC blip
            # during a restart isn't mistaken for a crash.
            if self._mpv_is_running():
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
            f"mpv engine died unexpectedly (exit {code}) — signalling recovery.",
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
            # A dead mpv process surfaces here as a missing/refused socket or a
            # recv EOF; confirm via the exit code and hand off to recovery.
            if self._notify_if_dead():
                return
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
            if self._notify_if_dead():
                return
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
