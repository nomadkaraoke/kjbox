# Plan: Runtime-switchable karaoke renderer (mpv / VLC)

**Created:** 2026-04-17
**Branch:** `feat/sess-20260417-2318-renderer-toggle`
**Status:** Ready to implement (Ring 1 confirmed) — user review questions 2 & 3 before PR
**Deadline:** Next show 2026-04-18 morning

## Background

Last night we shipped a bugfix for an mpv↔VLC ALSA race that was silencing
filler music after every karaoke track (see
`docs/AUDIO.md § Filler Audio Handoff`). Then we discovered a CDG file didn't
show lyrics on screen under mpv in that specific environment, so we rolled
karaoke playback back to the legacy dual-VLC path to get through the show.

Both mpv and VLC **should** play CDG + MP4 correctly — mpv's historical CDG
timing bug ([mpv#3027](https://github.com/mpv-player/mpv/issues/3027)) was
closed and the user has observed mpv rendering CDG elsewhere. The goal isn't
to pick a winner; it's to give the KJ a **fast escape hatch** if any given
file (or environmental quirk) misbehaves on one renderer.

Deeper CDG-renderer testing is planned for tomorrow morning once NomadPC is
back on. This plan ships the toggle so that testing is useful: swap between
engines in seconds without a service restart.

## Goals

1. **Runtime renderer toggle** exposed in the AV Output modal — mpv / VLC
   radio or segmented control
2. **mpv is the default** for new installs, persisted in `config.json`
3. **Filler VLC is not disturbed** when switching renderers (no audio drop
   between songs)
4. **Pitch UI auto-hides** when the active renderer doesn't support it (VLC
   mode)
5. **Reject** toggle requests while karaoke is actively playing (user
   confirmed 2026-04-17) — return a clear error; KJ stops first, then
   switches
6. **No service restart** required to switch
7. Along the way, clean up:
   - ~110 lines of duplicated filler-VLC code across `vlc.py` and
     `mpv_manager.py`
   - Latent dead code in `routes.py:416-421` that calls mpv-only methods on
     `current_app.vlc` (would `AttributeError` on the fadeout action now
     that we're on VLCManager)
   - `audio_monitor.py` reaching into `mpv.audio_backend` directly — needs
     to talk to a stable interface that survives a renderer swap

## Non-goals (explicitly out of scope for tomorrow)

- Pre-converting the MP3+CDG library to MP4 (considered for a future ring
  if CDG testing reveals mpv has real problems with our files)
- Auto-routing per file extension (MP4→mpv, CDG→VLC). Adds policy and test
  surface; user wants manual control first
- Bundling the toggle with sweeping type-hint additions across the codebase

## Current surface (from 2026-04-17 research)

Everything treats the manager as `current_app.vlc`. Filler methods are
duplicated verbatim across `vlc.py` and `mpv_manager.py` (~110 lines:
`_probe_vlc`, `_kill_port`, `send_command`, `fade_music`, `fade_in_filler`,
`fade_out_filler`, `ensure_filler_stopped`, and the filler arm of
`_launch_vlc_filler` / `launch_instance`). Audio-monitor writes
`mpv.audio_backend` directly and calls `mpv.restart_instances()` — only
MpvManager has the attribute, so flipping to VLCManager with the audio
monitor on would silently do nothing (or error).

A latent bug lives in `routes.py:416-421`: the fadeout action calls
`vlc._vlc_to_mpv_volume(...)` and `vlc._set_property(...)`. Those are
MpvManager-only. With VLCManager active (today's state), that code path
would crash the first time a KJ hits the fadeout button. Fix this
polymorphically as part of Ring 1.

## Proposed architecture

```
                     ┌──────────────────────────┐
                     │  PlaybackCoordinator     │  ← current_app.vlc points here
                     │  (routes call this)      │
                     └────────────┬─────────────┘
                                  │
                  ┌───────────────┴──────────────────┐
                  ▼                                  ▼
         ┌──────────────────┐           ┌──────────────────────┐
         │  FillerVLC       │           │  KaraokePlayer       │
         │  (shared,        │           │  (protocol / ABC)    │
         │  always on)      │           └──┬─────────────────┬─┘
         │  - launch        │              │                 │
         │  - fade_in/out   │              ▼                 ▼
         │  - probe         │    ┌──────────────────┐  ┌────────────────────┐
         │  - ensure_stop   │    │ MpvKaraokePlayer │  │ VlcKaraokePlayer   │
         └──────────────────┘    │ (rubberband,     │  │ (dual-VLC legacy,  │
                                 │  IPC, pitch)     │  │  CDG-tested)       │
                                 └──────────────────┘  └────────────────────┘
```

**`PlaybackCoordinator`** owns the filler, one karaoke player at a time,
and the render-mode setting. `switch_renderer(mode)` tears down the current
karaoke player and constructs the other; the filler keeps running. The
coordinator exposes the surface `routes.py` already uses, so most routes
don't change mechanically.

**`KaraokePlayer` protocol** formalises the contract both backends
implement. `runtime_checkable` so the coordinator can assert conformance
in its constructor.

```python
class KaraokePlayer(Protocol):
    name: str                       # 'mpv' | 'vlc'
    supports_pitch: bool
    supports_cdg: bool              # both True; kept for future
    active: bool
    current_path: str | None
    volume: int                     # VLC scale 0-512
    pitch_semitones: int

    def play(self, file_path, display_path=None, overlay_manager=None): ...
    def stop(self): ...
    def seek(self, seconds: int): ...
    def pause_resume(self) -> bool | None: ...
    def set_volume(self, vlc_level: int): ...
    def get_status(self) -> dict: ...      # {state, time, length}
    def set_pitch(self, semitones: int): ...  # no-op on VLC
    def fadeout(self, duration_s: float = 3.0): ...  # fixes dead code
    def ensure_released(self): ...
    def monitor(self) -> None: ...          # blocking, run on a thread
    def try_reconnect(self) -> bool: ...
    def shutdown(self): ...                 # for renderer swap / sleep
```

**`FillerVLC`** owns the port-8081 VLC, its `audio_backend` setting
(alsa / pulse), and the state-file entry for `current_filler_track`. This
is the single place the audio monitor pokes for its pipewire backend flip.

## Phasing

Three concentric rings. **Ring 1 is committed for tomorrow's show.**
Rings 2 & 3 are bundled as follow-up unless time remains after acceptance.

### Ring 1 — Toggle ships (MVP, confirmed)

1. **Add `render_mode` to `config.json`** (default `'mpv'`)
2. **Create `kj-controller/filler.py`** with a `FillerVLC` class. Port the
   shared filler code from `mpv_manager.py` (the more-current copy). Both
   karaoke players receive a `FillerVLC` instance and delegate fade / probe
   / launch / shutdown. Deletes ~110 lines of duplication.
3. **Create `kj-controller/karaoke_player.py`** with the `KaraokePlayer`
   Protocol.
4. **Rework `vlc.py` → `VlcKaraokePlayer`** — implements the protocol,
   keeps the dual-VLC mechanics (port 8080 karaoke + inject shared Filler),
   `supports_pitch=False`, `set_pitch` logs and no-ops. Strip the filler
   methods (now in `FillerVLC`).
5. **Rework `mpv_manager.py` → `MpvKaraokePlayer`** — implements the
   protocol, keeps `_wait_for_mpv_idle` + `_verify_filler_playing`
   auto-heal, `supports_pitch=True`. Strip the filler methods.
6. **Create `kj-controller/playback.py`** with `PlaybackCoordinator`.
   Constructor: `(cfg, filler: FillerVLC, initial_mode: str)`. Methods:
   all the current `vlc.<method>` surface, delegating to either filler
   or the active karaoke player as appropriate. `switch_renderer(mode)`
   rejects if `self.player.active`; otherwise `player.shutdown()`, build
   new player, start its monitor thread, persist mode.
7. **`app.py`** builds one `PlaybackCoordinator` and assigns to
   `flask_app.vlc`. `start_app()` starts both the filler's reconnect+launch
   and the player's reconnect+launch via the coordinator.
8. **`routes.py`**
   - Add `GET /renderer` → `{mode, supports_pitch, supports_cdg, available_modes}`
   - Add `POST /renderer {mode}` → calls `coordinator.switch_renderer()`,
     persists to config, returns new state. Rejection produces HTTP 409
     with `{"error": "karaoke_active", "message": "Stop playback before
     switching renderer."}`.
   - Fix the fadeout dead code: call `vlc.fadeout(duration)` on the
     coordinator; both players implement it properly.
9. **`audio_monitor.py`** — replace `self.mpv.audio_backend = ...` with
   `self.coordinator.set_audio_backend(...)` which fans out to filler +
   current player. Restart-instances also routed through the coordinator.
10. **UI in `templates/index.html`** + `static/app.js`:
    - Renderer segmented control in the AV Output modal (next to Audio
      Device picker)
    - Show/hide pitch control in Now Playing bar based on
      `/renderer.supports_pitch`
    - Small "Engine: mpv" / "Engine: VLC" badge in Now Playing bar (TBD
      per user answer to Q3)
    - Client-side error toast if the server returns 409 during a switch
11. **Tests**:
    - `test_filler.py` — constructor, fade_in/out, ensure_stopped, probe,
      send_command error paths
    - `test_playback_coordinator.py` — happy-path swap, rejection during
      active playback, filler untouched across swap, mode persistence
    - `test_karaoke_player_protocol.py` — parametric fixture that exercises
      both players through the protocol; pure read-only behaviours share
      test bodies
    - Trim now-duplicated assertions from `test_vlc.py` /
      `test_mpv_manager.py`
    - Add `/renderer` routes to `test_av_routes.py`

**Manual acceptance (on nomadpc, 2026-04-18 AM):**

- Boot with default config → mpv active, pitch visible, MP4 plays with ±3
  semitone pitch
- Toggle to VLC from AV modal while stopped → 8080 karaoke VLC launches,
  pitch hides, filler keeps playing without interruption
- Play a CDG ZIP through VLC → lyrics visible
- Toggle to VLC while a track is playing → UI shows rejection toast,
  nothing changes
- Stop, toggle to mpv, play the same CDG ZIP → confirm whether mpv renders
  lyrics here (this is the actual investigation the user wants to do)
- Hit the fadeout button under mpv AND under VLC → both must work (fixes
  latent crash)
- Service restart → boots in whatever mode was last persisted

### Ring 2 — SOLID polish (follow-up PR)

- `kj-controller/state.py` — single STATE_FILE reader/writer. Today both
  managers open-code this with slightly different schemas
- Orphan-process cleanup on renderer switch (we hit this last night;
  `start_new_session=True` lets children outlive the systemd unit)
- `mypy --strict` on the new modules only (no codebase-wide bleed)
- Reconnect chooses renderer based on what's actually running (mpv socket
  present? VLC 8080 present?) rather than trusting config blindly

### Ring 3 — Debt paid down (stretch, separate session)

- CDG-specific testing infra: scripted playback of known-good CDG fixtures
  against both renderers to catch regressions
- Optional ingest-time pre-convert MP3+CDG → MP4 (unlocks pitch-shift on
  CDG tracks if mpv turns out to be reliable for CDG in practice)
- Telemetry: log renderer switches with reason so we learn what fails live

## Files to create / modify (Ring 1)

| File | Action | Notes |
|---|---|---|
| `kj-controller/filler.py` | Create | FillerVLC class |
| `kj-controller/karaoke_player.py` | Create | Protocol / ABC |
| `kj-controller/playback.py` | Create | PlaybackCoordinator |
| `kj-controller/vlc.py` | Rewrite | → VlcKaraokePlayer, filler stripped |
| `kj-controller/mpv_manager.py` | Rewrite | → MpvKaraokePlayer, filler stripped |
| `kj-controller/app.py` | Modify | Build coordinator; wire filler + player |
| `kj-controller/routes.py` | Modify | `/renderer` routes; polymorphic fadeout |
| `kj-controller/audio_monitor.py` | Modify | Talk to coordinator, not mpv internals |
| `kj-controller/config.py` | Modify | `render_mode` default + validation |
| `kj-controller/config.json` | Modify | `"render_mode": "mpv"` |
| `kj-controller/templates/index.html` | Modify | Renderer control in AV modal |
| `kj-controller/static/app.js` | Modify | Handler + pitch show/hide + error toast |
| `kj-controller/static/style.css` | Modify | Minor styling |
| `kj-controller/tests/unit/test_filler.py` | Create | |
| `kj-controller/tests/unit/test_playback_coordinator.py` | Create | |
| `kj-controller/tests/unit/test_karaoke_player_protocol.py` | Create | |
| `kj-controller/tests/unit/test_vlc.py` | Modify | Drop duplicated assertions |
| `kj-controller/tests/unit/test_mpv_manager.py` | Modify | Drop duplicated assertions |
| `kj-controller/tests/unit/test_audio_monitor.py` | Modify | Update coupling |
| `kj-controller/tests/integration/test_av_routes.py` | Modify | `/renderer` cases |
| `docs/AUDIO.md` | Modify | Document toggle + renderer capabilities matrix |
| `docs/ARCHITECTURE.md` | Modify | Replace manager section with coordinator + players |
| `docs/CHANGELOG.md` | Modify | Dated entry |
| `kj-controller/pyproject.toml` | Modify | Version 0.19.2 → 0.20.0 (minor — new feature) |

## Open questions (need user answer before merge)

Answered:
- ✅ **Reject switch during active playback?** Yes. Clean error, KJ stops
  first.

Still open:
- **Q2. Persistence** — `render_mode` in `config.json` (survives restart)
  vs session-only reset. _Recommendation: persist. Matches the audio
  device picker and avoids surprise mode changes after a restart._
- **Q3. Engine badge** in the Now Playing bar? Subtle "mpv" / "VLC" text
  next to the pitch control. _Recommendation: yes, small muted text —
  KJ wants to know what's active without opening the modal, especially
  after a restart._

## Risk

- **Hot path touched** — every /play goes through the coordinator. Mitigated
  by keeping player behaviour byte-for-byte identical, integration tests
  on the main flows, manual acceptance gated before the show.
- **Audio-monitor coupling change** — small but real. Its own unit tests
  (`test_audio_monitor.py`) must still pass; will adjust as needed.
- **Deploy touches Python** — requires `systemctl restart kj-controller`,
  which kills filler for ~2s. Plan to land mid-day when NomadPC is idle.

## Rollback

Worktree lands as a single PR. Pre-PR tag: `pre-renderer-toggle` at current
`origin/main` (42089af). If acceptance fails the morning of the show,
revert-merge the PR; no force-pushes, no `git reset --hard`.

## Estimate

Ring 1: **4–6 hours** focused. Could compress with aggressive scope cut
(skip the FillerVLC extraction, just add the coordinator around the two
existing managers) but that leaves the duplication the user explicitly
asked us to clean up, so I recommend against.
