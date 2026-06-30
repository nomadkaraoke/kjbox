"""KaraokePlayer protocol: contract for swappable karaoke playback backends.

Both `VlcKaraokePlayer` (dual-VLC, CDG-compatible) and `MpvKaraokePlayer`
(rubberband pitch, IPC) implement this. The `PlaybackCoordinator` holds
exactly one at a time and can swap between them at runtime.

Filler music playback is NOT part of this protocol — it's owned by
`FillerVLC` and shared across renderer swaps so filler keeps playing
uninterrupted when the KJ flips the toggle.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KaraokePlayer(Protocol):
    """Minimum surface every karaoke backend must provide."""

    # ── Identity & capabilities ────────────────────────────────────────
    name: str                   # 'mpv' | 'vlc' — matches config.RENDER_MODES
    supports_pitch: bool        # True if set_pitch() is honored
    supports_cdg: bool          # Intent, not hard guarantee (see docs/AUDIO.md)

    # ── Playback state ─────────────────────────────────────────────────
    active: bool                # True between play() and stop()/EOF
    current_path: str | None    # Display path of currently-loaded track
    volume: int                 # VLC-scale (0-512); 256 = 100%
    pitch_semitones: int        # Always 0 on players without pitch support
    audio_error: bool           # Last play() did not produce audio
    audio_device: str           # ALSA device name, e.g. 'hdmiout'

    # ── Lifecycle ──────────────────────────────────────────────────────

    def launch(self) -> None:
        """Start the underlying player process (idempotent if already running)."""

    def try_reconnect(self) -> bool:
        """Attach to an existing process that survived a service restart.

        Returns True if a running instance was found and adopted.
        """

    def shutdown(self) -> None:
        """Terminate the underlying process. Used by the coordinator during
        a renderer swap. Must leave the system in a state where the other
        renderer can claim the audio device."""

    # ── Playback control ───────────────────────────────────────────────

    def play(self, file_path: str, display_path: str | None = None,
             overlay_manager=None, audio_file: str | None = None) -> None:
        """Load and start playback. Caller is responsible for stopping filler
        before calling (the coordinator does this).

        ``audio_file`` optionally supplies an external audio track (used for
        CDG zips on the mpv renderer, where the .cdg is the main file and the
        .mp3 is attached as audio). Renderers that auto-discover sibling audio
        (VLC) may ignore it."""

    def stop(self) -> None:
        """Stop playback, release the audio device, clear playback state."""

    def seek(self, seconds: int) -> None:
        """Seek to absolute position in seconds."""

    def pause_resume(self) -> bool | None:
        """Toggle pause. Returns True if now paused, False if now playing,
        None on error (e.g. player unreachable)."""

    def set_volume(self, vlc_level: int) -> None:
        """Set live karaoke volume (VLC scale 0-512)."""

    def set_pitch(self, semitones: int) -> None:
        """Set pitch offset in semitones. No-op on backends without pitch support."""

    def fadeout(self, duration_s: float = 3.0) -> None:
        """Fade volume to 0 over duration_s, then stop. Restores configured
        volume afterward so the next song starts at expected level."""

    def get_status(self) -> dict:
        """Return playback status dict: {state, time, length}."""

    def ensure_released(self) -> bool:
        """Stop playback and confirm the audio device has actually been
        released. Used before filler reclaims hdmiout."""

    # ── Monitor loop ───────────────────────────────────────────────────

    def monitor(self) -> None:
        """Blocking loop. Fires `on_karaoke_end` when playback ends.

        Runs on a background thread started by the coordinator. The player
        should exit this loop (or raise) on shutdown() so the coordinator
        can swap renderers cleanly.
        """

    # ── End-of-song callback ───────────────────────────────────────────
    on_karaoke_end: object  # Callable[[], None] | None
