"""PlaybackCoordinator: facade that routes.py talks to.

Owns one shared `FillerVLC` and one active `KaraokePlayer` (mpv or VLC).
`switch_renderer(mode)` tears down the current karaoke player and builds
the other, leaving the filler VLC untouched so filler music doesn't drop
when the KJ flips the toggle.

Routes call the coordinator as if it were the old manager — most of the
`vlc.<method>` / `vlc.<attr>` surface is preserved.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

from config import (
    DEFAULT_RENDER_MODE,
    RENDER_MODE_MPV,
    RENDER_MODE_VLC,
    RENDER_MODES,
    is_pi,
    save_config_value,
)
from filler import FillerVLC
from karaoke_player import KaraokePlayer
from mpv_manager import MpvKaraokePlayer
from utils import log_message
from vlc import VlcKaraokePlayer


class RendererSwitchRejected(Exception):
    """Raised when switch_renderer() is called while karaoke is active."""
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


# Restart-loop guard: if the engine crashes this many times within the window
# — regardless of which song, so it also catches a hot restart loop or a run of
# un-playable files — stop auto-restarting and escalate to the operator (switch
# engines / intervene) instead of thrashing. The song is used only for context.
CRASH_GUARD_WINDOW = 60.0   # seconds
CRASH_GUARD_MAX = 3


class PlaybackCoordinator:
    """Coordinates filler + one karaoke player; handles runtime renderer swaps."""

    def __init__(self, config, enabled=None, overlay_manager=None,
                 initial_mode=None, audio_backend='alsa'):
        self.config = config
        if enabled is None:
            enabled = is_pi() or config.get('enable_vlc', False)
        self.enabled = enabled
        self.overlay_manager = overlay_manager

        self.render_mode = initial_mode or config.get('render_mode', DEFAULT_RENDER_MODE)
        if self.render_mode not in RENDER_MODES:
            log_message(
                f"Invalid render_mode '{self.render_mode}' in config; "
                f"falling back to '{DEFAULT_RENDER_MODE}'.",
                config,
            )
            self.render_mode = DEFAULT_RENDER_MODE

        self.audio_backend = audio_backend
        # Engine-crash health tracking (see _handle_engine_died / restart guard).
        self._health_lock = threading.Lock()
        self._restart_lock = threading.Lock()  # serialise auto-restarts (no overlap)
        self._health_events = deque(maxlen=30)
        self._crash_history = deque(maxlen=30)  # (ts, song) for the restart guard
        self._event_seq = 0
        self._last_acked_id = 0
        self.filler = FillerVLC(config, enabled=enabled, audio_backend=audio_backend)
        self.player: KaraokePlayer = self._build_player(self.render_mode)
        self._monitor_thread = None

    # ── Renderer construction ──────────────────────────────────────────

    def _build_player(self, mode: str) -> KaraokePlayer:
        if mode == RENDER_MODE_MPV:
            player: KaraokePlayer = MpvKaraokePlayer(
                self.config, self.filler, enabled=self.enabled,
                audio_backend=self.audio_backend,
            )
        elif mode == RENDER_MODE_VLC:
            player = VlcKaraokePlayer(self.config, self.filler, enabled=self.enabled)
        else:
            raise ValueError(f"Unknown render mode: {mode}")
        # Route every engine's crash callback to the coordinator so a dead
        # player (mpv or vlc) is auto-restarted + surfaced to the operator.
        player.on_engine_died = self._handle_engine_died
        return player

    # ── Engine crash detection → recovery + operator notification ──────
    def _handle_engine_died(self, info):
        """Fired by a player when its process crashes. Records the event, then
        either auto-restarts (on a separate thread — restart_instances blocks
        several seconds) or, if the same song keeps crashing, escalates to the
        operator instead of thrashing."""
        escalate = self._record_crash(info)
        if escalate:
            return
        threading.Thread(
            target=self._safe_restart, name='engine-recovery', daemon=True,
        ).start()

    def _record_crash(self, info) -> bool:
        """Record a crash event and decide restart-vs-escalate. Returns True to
        escalate (>= CRASH_GUARD_MAX engine crashes within the window, regardless
        of song), False to auto-restart."""
        info = info or {}
        song = info.get('song')
        engine = info.get('engine', self.render_mode)
        now = time.time()
        with self._health_lock:
            self._crash_history.append(now)
            recent = sum(1 for t in self._crash_history if now - t <= CRASH_GUARD_WINDOW)
            escalate = recent >= CRASH_GUARD_MAX
            self._event_seq += 1
            self._health_events.append({
                'id': self._event_seq,
                'ts': now,
                'engine': engine,
                'song': song,
                'file_path': info.get('file_path'),
                'outcome': 'escalated' if escalate else 'restarting',
            })
        log_message(
            f"Video player ({engine}) crashed on '{song}' — "
            + ("escalated: repeated crashes on the same song, operator action "
               "needed" if escalate else "auto-restarting"),
            self.config,
        )
        return escalate

    def _safe_restart(self):
        # Serialise auto-restarts: if one is already in flight, skip this one so
        # concurrent crash callbacks can't run overlapping restart_instances()
        # against the shared player/filler state.
        if not self._restart_lock.acquire(blocking=False):
            log_message("Auto-restart already in progress — skipping duplicate.", self.config)
            return
        try:
            self.restart_instances()
        except Exception as e:
            log_message(f"Auto-restart after engine crash failed: {e}", self.config)
        finally:
            self._restart_lock.release()

    @property
    def player_alert(self):
        """Latest un-acknowledged crash event (drives the KJ banner), or None."""
        with self._health_lock:
            for ev in reversed(self._health_events):
                if ev['id'] > self._last_acked_id:
                    return dict(ev)
            return None

    @property
    def player_health_events(self):
        """Recent crash events (bounded), oldest first — for history/logging."""
        with self._health_lock:
            return [dict(ev) for ev in self._health_events]

    def ack_player_alerts(self, up_to_id):
        """Operator dismissed the banner: acknowledge all events up to this id."""
        with self._health_lock:
            try:
                self._last_acked_id = max(self._last_acked_id, int(up_to_id))
            except (TypeError, ValueError):
                pass

    # ── Public renderer API ────────────────────────────────────────────

    def describe_renderer(self) -> dict:
        """Return renderer metadata for GET /renderer."""
        return {
            'mode': self.render_mode,
            'supports_pitch': self.player.supports_pitch,
            'supports_cdg': self.player.supports_cdg,
            'available_modes': list(RENDER_MODES),
        }

    def get_perf(self):
        """Engine-agnostic perf snapshot for the monitor: {engine, **player perf}.

        `engine` is the active renderer name while a song plays, else None (idle).
        Never raises — a misbehaving player yields an idle-shaped dict.
        """
        try:
            p = self.player.get_perf()
        except Exception:
            p = {"playing": False}
        return {"engine": self.render_mode if p.get("playing") else None, **p}

    def switch_renderer(self, mode: str) -> dict:
        """Swap renderers while filler keeps playing. Rejects if karaoke is
        currently active (user must stop first)."""
        if mode not in RENDER_MODES:
            raise ValueError(f"Unknown render mode: {mode}")
        if mode == self.render_mode:
            return self.describe_renderer()
        if self.player.active:
            raise RendererSwitchRejected(
                "Stop karaoke playback before switching renderer."
            )

        log_message(
            f"Switching renderer: {self.render_mode} → {mode}...",
            self.config,
        )
        old_player = self.player
        # Capture state that would otherwise be lost in the rebuilt player
        # (new instance re-reads defaults from config — filler survives because
        # it is shutdown-then-relaunched on the same instance).
        on_end = old_player.on_karaoke_end
        old_audio_device = old_player.audio_device
        old_player.shutdown()

        self.render_mode = mode
        self.player = self._build_player(mode)
        self.player.on_karaoke_end = on_end
        self.player.audio_device = old_audio_device
        self.player.launch()

        # Start a fresh monitor thread for the new player
        self._start_monitor()

        # Persist so restarts pick up the last choice
        try:
            save_config_value('render_mode', mode)
        except Exception as e:
            log_message(f"Failed to persist render_mode: {e}", self.config)

        log_message(f"Renderer switched to '{mode}'.", self.config)
        return self.describe_renderer()

    # ── Audio backend ──────────────────────────────────────────────────

    def set_audio_backend(self, backend: str) -> None:
        """Flip both filler and player to the given audio backend (alsa/pipewire).
        The audio monitor uses this when starting/stopping."""
        self.audio_backend = backend
        self.filler.audio_backend = backend
        # MpvKaraokePlayer has an audio_backend that it reads at launch
        if hasattr(self.player, 'audio_backend'):
            self.player.audio_backend = backend

    # ── Startup / reconnect ────────────────────────────────────────────

    def init_playback(self):
        """Called from start_app(): reconnect to running instances, launch
        missing ones, start the monitor thread, fade in filler."""
        if not self.enabled:
            return

        filler_found = self.filler.try_reconnect()
        player_found = self.player.try_reconnect()

        if not player_found:
            self.player.launch()

        if not filler_found:
            self.filler.launch(media_file=self.filler.current_media_path(), loop=True)

        # Only fade in filler if karaoke isn't actively playing
        if not self.player.active:
            if not filler_found:
                time.sleep(3)
            self.filler.fade_in()
        else:
            log_message("Karaoke is active — skipping filler fade-in.", self.config)

        self._start_monitor()

    def _start_monitor(self):
        """Run player.monitor() on a fresh daemon thread. Shuts down cleanly
        when player.shutdown() sets the stop event."""
        t = threading.Thread(target=self.player.monitor, daemon=True)
        t.start()
        self._monitor_thread = t

    def restart_instances(self):
        """Nuke and relaunch both filler + karaoke player. Invoked by
        /fix_audio and audio-device switches."""
        if not self.enabled:
            return

        log_message(
            f"Restarting playback instances with audio device '{self.audio_device}'...",
            self.config,
        )

        # Preserve state that would be lost in the rebuilt player. Without this,
        # a runtime audio-device override (e.g. from /av/vlc-device) would revert
        # to config.default_audio_device because the new MpvKaraokePlayer /
        # VlcKaraokePlayer __init__ re-reads it from config. The filler survives
        # naturally — it is shutdown-then-relaunched on the same instance.
        on_end = self.player.on_karaoke_end
        old_audio_device = self.player.audio_device

        # Shut down player (clears its monitor thread)
        self.player.shutdown()
        self.filler.shutdown()
        time.sleep(1)

        # Re-build player (the shutdown'd one has its stop event set)
        self.player = self._build_player(self.render_mode)
        self.player.on_karaoke_end = on_end
        self.player.audio_device = old_audio_device

        self.player.launch()
        self.filler.launch(media_file=self.filler.current_media_path(), loop=True)
        time.sleep(3)
        self.filler.fade_in()
        self._start_monitor()
        log_message("Playback instances restarted successfully.", self.config)

    # ── routes.py-facing surface (API compat with old VLCManager) ─────

    # -- Identity/capability passthrough --
    @property
    def supports_pitch(self) -> bool:
        return self.player.supports_pitch

    @property
    def supports_cdg(self) -> bool:
        return self.player.supports_cdg

    # -- Karaoke state passthrough --
    @property
    def karaoke_active(self) -> bool:
        return self.player.active

    @karaoke_active.setter
    def karaoke_active(self, value: bool):
        # Routes sometimes assign this directly (e.g. /control pause handler)
        self.player.active = bool(value)

    @property
    def current_playing_path(self):
        return self.player.current_path

    @current_playing_path.setter
    def current_playing_path(self, value):
        self.player.current_path = value

    @property
    def pitch_semitones(self) -> int:
        return self.player.pitch_semitones

    @property
    def karaoke_volume(self) -> int:
        return self.player.karaoke_volume

    @karaoke_volume.setter
    def karaoke_volume(self, value: int):
        self.player.karaoke_volume = int(value)

    @property
    def audio_error(self) -> bool:
        return self.player.audio_error

    @audio_error.setter
    def audio_error(self, value: bool):
        self.player.audio_error = bool(value)

    @property
    def audio_device(self) -> str:
        return self.player.audio_device

    @audio_device.setter
    def audio_device(self, value: str):
        self.player.audio_device = value
        self.filler.audio_device = value

    # -- Filler passthrough --
    @property
    def filler_volume(self) -> int:
        return self.filler.volume

    @filler_volume.setter
    def filler_volume(self, value: int):
        self.filler.volume = int(value)

    @property
    def current_filler_track(self):
        return self.filler.current_track

    @current_filler_track.setter
    def current_filler_track(self, value):
        self.filler.current_track = value

    # -- Callback passthrough --
    @property
    def on_karaoke_end(self):
        return self.player.on_karaoke_end

    @on_karaoke_end.setter
    def on_karaoke_end(self, fn):
        self.player.on_karaoke_end = fn

    # -- Karaoke playback --
    def play_video(self, file_path, display_path=None, overlay_manager=None, audio_file=None):
        """Stop filler, then play on the current renderer.

        ``audio_file`` is forwarded to the player (external audio track for CDG
        zips on mpv); see ``KaraokePlayer.play``."""
        if not self.enabled:
            log_message(f"Playback disabled — cannot play {os.path.basename(file_path)}", self.config)
            return

        overlay = overlay_manager or self.overlay_manager

        # Fade out filler if still playing
        filler_status = self.filler.send("")
        if not filler_status or filler_status.get('state') != 'stopped':
            self.filler.fade_out()
            time.sleep(0.3)
            self.filler.ensure_stopped()

        self.player.play(file_path, display_path=display_path, overlay_manager=overlay,
                         audio_file=audio_file)

    def stop_karaoke(self):
        self.player.stop()

    def seek_karaoke(self, seconds: int):
        self.player.seek(seconds)

    def pause_resume_karaoke(self):
        return self.player.pause_resume()

    def set_karaoke_volume_live(self, vlc_level: int):
        self.player.set_volume(vlc_level)

    def get_karaoke_status(self):
        return self.player.get_status()

    def set_pitch(self, semitones: int):
        self.player.set_pitch(semitones)

    def fadeout(self, duration_s: float = 3.0):
        """Polymorphic karaoke fadeout — replaces the dead mpv-specific code
        in routes.py. Coordinator orchestrates the post-fadeout filler fade-in
        so both players behave the same."""
        def _do():
            self.player.fadeout(duration_s=duration_s)
            # Wait for the player's fadeout thread to finish
            time.sleep(duration_s + 0.5)
            self.player.ensure_released()
            if self.overlay_manager is not None:
                self.overlay_manager.set_karaoke_playing(False)
            self.filler.fade_in()
            log_message("Coordinator fadeout complete, filler faded in.", self.config)

        threading.Thread(target=_do, daemon=True).start()

    def ensure_karaoke_released(self):
        return self.player.ensure_released()

    # -- Filler control --
    def fade_in_filler(self):
        self.filler.fade_in()

    def fade_out_filler(self):
        self.filler.fade_out()

    def ensure_filler_stopped(self):
        return self.filler.ensure_stopped()

    def send_command(self, port, password, command, is_path=False, debug=False):
        """Back-compat shim: routes.py /volume uses this to set filler volume.

        Only supports the filler port (8081). For other targets, prefer the
        more specific coordinator methods.
        """
        filler_port = self.config.get('filler_vlc_port', 8081)
        if port == filler_port:
            return self.filler.send(command, is_path=is_path, debug=debug)
        log_message(
            f"send_command to unknown port {port} — coordinator only proxies filler.",
            self.config,
        )
        return None

    # -- Sleep mode needs process handles --
    @property
    def processes(self) -> dict:
        """sleep_mode.py reads this to terminate long-running subprocesses."""
        procs = {}
        if hasattr(self.player, 'process'):
            procs['karaoke'] = self.player.process
        procs['filler'] = self.filler.process
        return procs

    # -- Reconnect --
    def try_reconnect(self):
        """Back-compat: returns dict {karaoke, filler} like old managers."""
        if not self.enabled:
            return {'karaoke': False, 'filler': False}
        return {
            'karaoke': self.player.try_reconnect(),
            'filler': self.filler.try_reconnect(),
        }

    # -- Monitor pass-through (for tests / external callers) --
    def monitor_karaoke(self):
        """Run the active player's monitor loop (blocking). Used by test
        fixtures; start_app() goes through init_playback → _start_monitor."""
        self.player.monitor()

    # -- State persistence (fan out to filler + player) --
    def _save_state(self):
        """Back-compat: route.py calls this after updating current_filler_track
        etc. Fans out to the filler and player state stores."""
        self.filler._save_state()
        if hasattr(self.player, '_save_state'):
            self.player._save_state()

    # -- launch_instance back-compat (used by start_app / tests) --
    def launch_instance(self, name, *args, **kwargs):
        if name == 'karaoke':
            self.player.launch()
        elif name == 'filler':
            media_file = kwargs.get('media_file')
            if media_file is None and len(args) >= 3:
                media_file = args[2]
            loop = kwargs.get('loop', True)
            if len(args) >= 4:
                loop = args[3]
            self.filler.launch(media_file=media_file, loop=loop)
