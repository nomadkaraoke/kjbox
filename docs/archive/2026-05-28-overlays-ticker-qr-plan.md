# Rotation Ticker + Scan-to-Sing QR — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a rotation-driven ticker mode and a one-click Scan-to-Sing QR preset to the existing overlay system, plus QR visual polish and a Z-order fix so QR sits above ticker.

**Architecture:** The Flask backend composes ticker text from the rotation snapshot on every `_after_mutation` and writes it into the ticker overlay's `config.text` field. The pygame overlay engine stays a dumb renderer of `config.text`. QR overlay gains `bg_opacity` + `corner_radius` and the engine restacks QR windows above any visible ticker.

**Tech Stack:** Python 3 + Flask backend, pygame-ce + Pillow + qrcode engine, vanilla JS frontend, pytest, SQLite (rotation store, unchanged).

**Spec:** `docs/archive/2026-05-28-overlays-ticker-qr-design.md`

---

## File map

**New files:**
- `kj-controller/rotation_ticker_sync.py` — `compose_ticker_text()` + `RotationTickerSync` class
- `kj-controller/tests/unit/test_compose_ticker_text.py`
- `kj-controller/tests/unit/test_overlay_presets.py`
- `kj-controller/tests/integration/test_rotation_ticker_hook.py`
- `kj-controller/tests/integration/test_overlay_presets_route.py`
- `kj-controller/tests/unit/test_qr_overlay_visual.py`
- `kj-controller/tests/unit/test_engine_restack.py`

**Modified files:**
- `kj-controller/overlay.py` — `OVERLAY_PRESETS`, `OverlayManager.create_preset()`
- `kj-controller/rotation.py` — accept `rotation_ticker_sync`, call refresh in `_after_mutation`
- `kj-controller/routes.py` — `POST /overlays/presets/<name>`, refresh hook in POST/PUT
- `kj-controller/app.py` — wire `RotationTickerSync` into factory
- `desktop/overlay_config.py` — defaults for new ticker + QR fields, loosen validate for `source=='rotation'`
- `desktop/overlay_types.py` — QR `bg_opacity` + `corner_radius`, restack helper
- `desktop/overlay_engine.py` — call `_restack_qr_above_ticker()` after reload + visibility change
- `kj-controller/templates/index.html` — Scan-to-Sing button, ticker Source select, conditional rotation fields
- `kj-controller/static/app.js` — `addScanToSingQR()`, updated `onOverlayTypeChange()`, ticker source change handler

---

## Task 1: `compose_ticker_text` pure function

**Files:**
- Create: `kj-controller/rotation_ticker_sync.py`
- Test: `kj-controller/tests/unit/test_compose_ticker_text.py`

- [ ] **Step 1: Write the failing test**

Create `kj-controller/tests/unit/test_compose_ticker_text.py`:

```python
"""Unit tests for compose_ticker_text — the pure ticker composition function."""

from rotation_ticker_sync import compose_ticker_text


def _entries(*names):
    return [{"singer": n, "song_artist": "", "status": "Waiting"} for n in names]


class TestComposeTickerText:
    def test_empty_rotation_returns_prefix_plus_empty_text(self):
        out = compose_ticker_text(
            entries=[],
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="Sign up at the booth!",
        )
        assert out == "Up next: Sign up at the booth!"

    def test_single_singer_numbered(self):
        out = compose_ticker_text(
            entries=_entries("Alice"),
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice"

    def test_five_singers_numbered_in_order(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob", "Carol", "Dave", "Eve"),
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice   2. Bob   3. Carol   4. Dave   5. Eve"

    def test_count_truncates_overflow(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob", "Carol", "Dave", "Eve", "Frank"),
            prefix="Up next: ",
            count=3,
            separator=" | ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice | 2. Bob | 3. Carol"

    def test_fewer_singers_than_count(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob"),
            prefix="Up next: ",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "Up next: 1. Alice   2. Bob"

    def test_custom_prefix_and_separator(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob"),
            prefix="Rotation >>> ",
            count=5,
            separator=" • ",
            empty_text="",
        )
        assert out == "Rotation >>> 1. Alice • 2. Bob"

    def test_empty_prefix(self):
        out = compose_ticker_text(
            entries=_entries("Alice"),
            prefix="",
            count=5,
            separator="   ",
            empty_text="",
        )
        assert out == "1. Alice"

    def test_unicode_singer_names_pass_through(self):
        out = compose_ticker_text(
            entries=_entries("Renée", "山田太郎", "🎤 Karaoke King"),
            prefix="Up next: ",
            count=5,
            separator=" • ",
            empty_text="",
        )
        assert out == "Up next: 1. Renée • 2. 山田太郎 • 3. 🎤 Karaoke King"

    def test_count_zero_returns_empty_message(self):
        out = compose_ticker_text(
            entries=_entries("Alice", "Bob"),
            prefix="Up next: ",
            count=0,
            separator="   ",
            empty_text="Empty",
        )
        # Count=0 means "no slots configured" — treat as empty rotation for the user.
        assert out == "Up next: Empty"
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-overlays-ticker-qr/kj-controller
pytest tests/unit/test_compose_ticker_text.py -v
```

Expected: `ModuleNotFoundError: No module named 'rotation_ticker_sync'`.

- [ ] **Step 3: Implement `compose_ticker_text`**

Create `kj-controller/rotation_ticker_sync.py`:

```python
"""Rotation ticker sync — composes ticker text from the rotation snapshot
and updates ticker overlays whose source is 'rotation'.

The engine stays a dumb renderer of config.text; this module is the only
place that knows how to derive that text from the rotation queue.
"""

import logging

logger = logging.getLogger(__name__)


def compose_ticker_text(entries, prefix, count, separator, empty_text):
    """Return the ticker text string for a rotation snapshot.

    Args:
        entries: list of rotation entries (each a dict with at least "singer").
            Caller is responsible for filtering done/left and ordering.
        prefix: text prepended once before the dynamic list.
        count: max singers to include. count<=0 is treated as empty.
        separator: string inserted between numbered slots.
        empty_text: shown after prefix when no singers fit.

    Returns:
        The composed string, e.g. "Up next: 1. Alice   2. Bob".
    """
    if count <= 0:
        return f"{prefix}{empty_text}"

    slice_ = entries[:count]
    if not slice_:
        return f"{prefix}{empty_text}"

    slots = [f"{i}. {e['singer']}" for i, e in enumerate(slice_, start=1)]
    return f"{prefix}{separator.join(slots)}"
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest tests/unit/test_compose_ticker_text.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_ticker_sync.py kj-controller/tests/unit/test_compose_ticker_text.py
git commit -m "feat(overlay): add compose_ticker_text pure function for rotation tickers"
```

---

## Task 2: Defaults for new fields in `overlay_config.py`

**Files:**
- Modify: `desktop/overlay_config.py`
- Modify (extend): `kj-controller/tests/unit/test_overlay_config.py`

- [ ] **Step 1: Add failing tests**

Append to `kj-controller/tests/unit/test_overlay_config.py`:

```python
class TestNewFieldDefaults:
    def test_ticker_gains_source_default(self):
        overlay = {"type": "ticker", "config": {}}
        apply_defaults(overlay)
        assert overlay["config"]["source"] == "static"
        assert overlay["config"]["prefix"] == "Up next: "
        assert overlay["config"]["count"] == 5
        assert overlay["config"]["separator"] == "   "
        assert overlay["config"]["empty_text"] == "Sign up at the booth!"

    def test_qr_gains_bg_opacity_and_corner_radius_defaults(self):
        overlay = {"type": "qr_code", "config": {}}
        apply_defaults(overlay)
        assert overlay["config"]["bg_opacity"] == 1.0
        assert overlay["config"]["corner_radius"] == 0

    def test_apply_defaults_preserves_explicit_values(self):
        overlay = {
            "type": "ticker",
            "config": {"source": "rotation", "count": 3, "prefix": "Queue: "},
        }
        apply_defaults(overlay)
        assert overlay["config"]["source"] == "rotation"
        assert overlay["config"]["count"] == 3
        assert overlay["config"]["prefix"] == "Queue: "


class TestValidateRotationTicker:
    def test_rotation_ticker_is_valid_without_text(self):
        valid, err = validate_overlay({
            "id": "x",
            "type": "ticker",
            "config": {"source": "rotation"},
        })
        assert valid, err

    def test_static_ticker_still_requires_text(self):
        valid, err = validate_overlay({
            "id": "x",
            "type": "ticker",
            "config": {"source": "static"},
        })
        assert not valid
```

- [ ] **Step 2: Run tests, verify failures**

```bash
pytest tests/unit/test_overlay_config.py::TestNewFieldDefaults tests/unit/test_overlay_config.py::TestValidateRotationTicker -v
```

Expected: 5 failures (missing default keys + validate_overlay rejects rotation ticker with no text).

- [ ] **Step 3: Update `TYPE_DEFAULTS` and `validate_overlay`**

In `desktop/overlay_config.py`, replace the `'ticker'` entry of `TYPE_DEFAULTS`:

```python
    'ticker': {
        'text': '',
        'speed': 2,
        'position': 'bottom',
        'font_size': 28,
        'text_color': '#FFFFFF',
        'bg_color': '#1a1a2e',
        'bg_opacity': 0.85,
        'padding': 10,
        # Rotation-driven ticker fields. Engine ignores them when source=='static'.
        'source': 'static',
        'prefix': 'Up next: ',
        'count': 5,
        'separator': '   ',
        'empty_text': 'Sign up at the booth!',
    },
```

Replace the `'qr_code'` entry of `TYPE_DEFAULTS`:

```python
    'qr_code': {
        'url': '',
        'label': '',
        'position': 'bottom-right',
        'custom_x': None,
        'custom_y': None,
        'size': 180,
        'padding': 10,
        # Visual polish so QR can sit on top of video/ticker.
        'bg_color': '#000000',
        'bg_opacity': 1.0,
        'corner_radius': 0,
    },
```

In `validate_overlay`, replace the ticker branch:

```python
    if overlay_type == 'ticker':
        source = config.get('source', 'static')
        if source == 'static' and not config.get('text'):
            return False, 'Static ticker overlay requires text'
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
pytest tests/unit/test_overlay_config.py -v
```

Expected: all tests pass (existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add desktop/overlay_config.py kj-controller/tests/unit/test_overlay_config.py
git commit -m "feat(overlay): defaults for rotation ticker + QR visual fields"
```

---

## Task 3: `OVERLAY_PRESETS` + `OverlayManager.create_preset()`

**Files:**
- Modify: `kj-controller/overlay.py`
- Create: `kj-controller/tests/unit/test_overlay_presets.py`

- [ ] **Step 1: Write the failing test**

Create `kj-controller/tests/unit/test_overlay_presets.py`:

```python
"""Unit tests for overlay presets — Scan-to-Sing and any future named presets."""

import pytest

from overlay import OVERLAY_PRESETS, OverlayManager


@pytest.fixture
def manager(tmp_path):
    return OverlayManager(config_path=str(tmp_path / 'overlays.json'))


class TestOverlayPresets:
    def test_scan_to_sing_preset_exists(self):
        assert 'scan-to-sing' in OVERLAY_PRESETS
        preset = OVERLAY_PRESETS['scan-to-sing']
        assert preset['type'] == 'qr_code'
        assert preset['show_over_video'] is True
        assert preset['enabled'] is True
        cfg = preset['config']
        assert cfg['follow_event_url'] is True
        assert cfg['position'] == 'top-right'
        assert cfg['size'] <= 140  # "quite small"
        assert 0.0 <= cfg['bg_opacity'] <= 1.0
        assert cfg['corner_radius'] >= 0

    def test_create_preset_returns_overlay_with_id(self, manager):
        overlay = manager.create_preset('scan-to-sing')
        assert overlay['id']
        assert overlay['type'] == 'qr_code'
        assert overlay['name'] == 'Scan to Sing'
        assert overlay['show_over_video'] is True
        assert overlay['config']['follow_event_url'] is True
        # Persisted
        assert manager.get_overlay(overlay['id']) is not None

    def test_create_preset_unknown_raises(self, manager):
        with pytest.raises(ValueError, match='Unknown preset'):
            manager.create_preset('does-not-exist')

    def test_create_preset_does_not_mutate_template(self, manager):
        first = manager.create_preset('scan-to-sing')
        second = manager.create_preset('scan-to-sing')
        # Each call gets a fresh deep-copied config and a new id
        assert first['id'] != second['id']
        assert first['config'] is not second['config']
        # Template is unchanged
        assert OVERLAY_PRESETS['scan-to-sing']['config'].get('url', '') == ''
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest tests/unit/test_overlay_presets.py -v
```

Expected: `ImportError: cannot import name 'OVERLAY_PRESETS'`.

- [ ] **Step 3: Implement preset support**

In `kj-controller/overlay.py`, at the top after the existing imports add:

```python
import copy
```

After the existing `OVERLAY_TYPES = {...}` declaration, add:

```python
OVERLAY_PRESETS = {
    'scan-to-sing': {
        'type': 'qr_code',
        'name': 'Scan to Sing',
        'enabled': True,
        'show_over_video': True,
        'config': {
            'url': '',  # Filled in by sync_event_url_overlays after creation
            'follow_event_url': True,
            'label': 'Scan to sing',
            'size': 110,
            'position': 'top-right',
            'padding': 8,
            'bg_color': '#000000',
            'bg_opacity': 0.85,
            'corner_radius': 12,
        },
    },
}
```

In the `OverlayManager` class, add a method (place after `import_overlays`):

```python
    def create_preset(self, preset_name):
        """Create a new overlay from a named preset. Returns the created overlay.

        Raises ValueError if the preset name is unknown.
        """
        if preset_name not in OVERLAY_PRESETS:
            raise ValueError(f'Unknown preset: {preset_name}')
        template = copy.deepcopy(OVERLAY_PRESETS[preset_name])
        return self.create_overlay(template)
```

- [ ] **Step 4: Run test, verify PASS**

```bash
pytest tests/unit/test_overlay_presets.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/overlay.py kj-controller/tests/unit/test_overlay_presets.py
git commit -m "feat(overlay): add Scan-to-Sing preset and OverlayManager.create_preset()"
```

---

## Task 4: `RotationTickerSync` class

**Files:**
- Modify: `kj-controller/rotation_ticker_sync.py`
- Modify: `kj-controller/tests/unit/test_compose_ticker_text.py` (rename later? no — add a new class)

- [ ] **Step 1: Write failing tests**

Append to `kj-controller/tests/unit/test_compose_ticker_text.py`:

```python
from unittest.mock import MagicMock

from rotation_ticker_sync import RotationTickerSync


def _ticker(oid, source='rotation', text='', prefix='Up next: ', count=5,
            separator='   ', empty_text='Sign up at the booth!'):
    return {
        'id': oid,
        'type': 'ticker',
        'name': '',
        'enabled': True,
        'show_over_video': True,
        'config': {
            'source': source,
            'text': text,
            'prefix': prefix,
            'count': count,
            'separator': separator,
            'empty_text': empty_text,
        },
    }


class TestRotationTickerSync:
    def test_refresh_no_rotation_tickers_is_noop(self):
        om = MagicMock()
        om.list_overlays.return_value = [_ticker('a', source='static', text='static text')]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 0
        om.update_overlay.assert_not_called()

    def test_refresh_updates_rotation_ticker_text(self):
        ticker = _ticker('t1')
        om = MagicMock()
        om.list_overlays.return_value = [ticker]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice', 'Bob')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 1
        om.update_overlay.assert_called_once()
        oid, updates = om.update_overlay.call_args.args
        assert oid == 't1'
        assert updates['config']['text'] == 'Up next: 1. Alice   2. Bob'

    def test_refresh_idempotent_when_text_unchanged(self):
        existing_text = 'Up next: 1. Alice'
        ticker = _ticker('t1', text=existing_text)
        om = MagicMock()
        om.list_overlays.return_value = [ticker]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 0
        om.update_overlay.assert_not_called()

    def test_refresh_updates_multiple_rotation_tickers(self):
        a = _ticker('a', prefix='A: ', count=1)
        b = _ticker('b', prefix='B: ', count=2)
        om = MagicMock()
        om.list_overlays.return_value = [a, b]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice', 'Bob', 'Carol')
        sync = RotationTickerSync(om, store)

        n = sync.refresh()
        assert n == 2
        calls = {call.args[0]: call.args[1] for call in om.update_overlay.call_args_list}
        assert calls['a']['config']['text'] == 'A: 1. Alice'
        assert calls['b']['config']['text'] == 'B: 1. Alice   2. Bob'

    def test_refresh_preserves_other_config_fields(self):
        ticker = _ticker('t1')
        ticker['config']['speed'] = 3
        ticker['config']['bg_color'] = '#abcdef'
        om = MagicMock()
        om.list_overlays.return_value = [ticker]
        store = MagicMock()
        store.get_entries.return_value = _entries('Alice')
        sync = RotationTickerSync(om, store)

        sync.refresh()
        updates = om.update_overlay.call_args.args[1]
        assert updates['config']['speed'] == 3
        assert updates['config']['bg_color'] == '#abcdef'

    def test_refresh_swallows_exceptions(self):
        om = MagicMock()
        om.list_overlays.side_effect = RuntimeError('boom')
        store = MagicMock()
        sync = RotationTickerSync(om, store)

        # Must not raise
        n = sync.refresh()
        assert n == 0
```

- [ ] **Step 2: Run tests, verify failure**

```bash
pytest tests/unit/test_compose_ticker_text.py::TestRotationTickerSync -v
```

Expected: `ImportError: cannot import name 'RotationTickerSync'`.

- [ ] **Step 3: Implement `RotationTickerSync`**

Append to `kj-controller/rotation_ticker_sync.py`:

```python
class RotationTickerSync:
    """Updates ticker overlays whose source is 'rotation' from the rotation queue.

    Hooked into RotationManager._after_mutation(). Best-effort: never raises.
    """

    def __init__(self, overlay_manager, rotation_store):
        self.overlay_manager = overlay_manager
        self.rotation_store = rotation_store

    def refresh(self):
        """Recompose text for every rotation ticker. Returns count updated."""
        try:
            overlays = self.overlay_manager.list_overlays()
        except Exception:
            logger.exception("rotation_ticker_sync: list_overlays failed")
            return 0

        try:
            entries = self.rotation_store.get_entries()
        except Exception:
            logger.exception("rotation_ticker_sync: get_entries failed")
            return 0

        updated = 0
        for overlay in overlays:
            if overlay.get('type') != 'ticker':
                continue
            cfg = overlay.get('config') or {}
            if cfg.get('source') != 'rotation':
                continue

            new_text = compose_ticker_text(
                entries=entries,
                prefix=cfg.get('prefix', 'Up next: '),
                count=int(cfg.get('count', 5) or 0),
                separator=cfg.get('separator', '   '),
                empty_text=cfg.get('empty_text', ''),
            )
            if cfg.get('text') == new_text:
                continue  # No-op: avoid spurious file write

            new_cfg = dict(cfg)
            new_cfg['text'] = new_text
            try:
                self.overlay_manager.update_overlay(overlay['id'], {'config': new_cfg})
                updated += 1
            except Exception:
                logger.exception("rotation_ticker_sync: update_overlay failed")
        return updated
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
pytest tests/unit/test_compose_ticker_text.py -v
```

Expected: all tests pass (compose_ticker_text + RotationTickerSync).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_ticker_sync.py kj-controller/tests/unit/test_compose_ticker_text.py
git commit -m "feat(overlay): RotationTickerSync recomposes ticker text on rotation change"
```

---

## Task 5: Wire `RotationTickerSync` into `RotationManager._after_mutation`

**Files:**
- Modify: `kj-controller/rotation.py`
- Create: `kj-controller/tests/integration/test_rotation_ticker_hook.py`

- [ ] **Step 1: Write the failing test**

Create `kj-controller/tests/integration/test_rotation_ticker_hook.py`:

```python
"""Integration test — RotationManager._after_mutation fires rotation_ticker_sync.refresh."""

from unittest.mock import MagicMock


def test_mutation_invokes_rotation_ticker_sync(sing_app):
    sync = MagicMock()
    sing_app.rotation.rotation_ticker_sync = sync
    sing_app.rotation.add_entry("Alice", song_artist="Test Song")
    sync.refresh.assert_called()


def test_mutation_no_sync_no_op(sing_app):
    sing_app.rotation.rotation_ticker_sync = None
    # Should not raise
    sing_app.rotation.add_entry("Bob", song_artist="Another Song")


def test_sync_exception_does_not_block_mutation(sing_app):
    sync = MagicMock()
    sync.refresh.side_effect = RuntimeError("boom")
    sing_app.rotation.rotation_ticker_sync = sync
    # Must not raise even though refresh() blew up
    sing_app.rotation.add_entry("Carol", song_artist="Bohemian Rhapsody")
    entries = sing_app.rotation.get_rotation()
    assert any(e['singer'] == 'Carol' for e in entries)


def test_sync_wired_in_create_app(sing_app):
    """The factory must have attached a RotationTickerSync."""
    from rotation_ticker_sync import RotationTickerSync
    assert isinstance(sing_app.rotation.rotation_ticker_sync, RotationTickerSync)
```

- [ ] **Step 2: Run test, verify failures**

```bash
pytest tests/integration/test_rotation_ticker_hook.py -v
```

Expected: all 4 fail (`rotation_ticker_sync` attribute does not exist; factory doesn't wire it).

- [ ] **Step 3: Wire the hook into `RotationManager`**

In `kj-controller/rotation.py`, in `RotationManager.__init__` add the attribute:

```python
        self.push_dispatcher = None  # Set by app.py if Web Push is configured
        self.rotation_ticker_sync = None  # Set by app.py
```

In `_after_mutation`, after the push-dispatcher block and before the sync block, add:

```python
        if self.rotation_ticker_sync is not None:
            try:
                self.rotation_ticker_sync.refresh()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("rotation_ticker_sync refresh failed")
```

(The factory wiring lives in Task 6; this task is complete once the attribute exists and the hook fires when set.)

- [ ] **Step 4: Run the in-process tests, verify those pass**

```bash
pytest tests/integration/test_rotation_ticker_hook.py::test_mutation_invokes_rotation_ticker_sync tests/integration/test_rotation_ticker_hook.py::test_mutation_no_sync_no_op tests/integration/test_rotation_ticker_hook.py::test_sync_exception_does_not_block_mutation -v
```

Expected: 3 passed. The `test_sync_wired_in_create_app` test still fails — Task 6 covers it.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation.py kj-controller/tests/integration/test_rotation_ticker_hook.py
git commit -m "feat(rotation): fire rotation_ticker_sync.refresh on every mutation"
```

---

## Task 6: Wire `RotationTickerSync` into `app.py` factory

**Files:**
- Modify: `kj-controller/app.py`

- [ ] **Step 1: Confirm the failing test from Task 5**

```bash
pytest tests/integration/test_rotation_ticker_hook.py::test_sync_wired_in_create_app -v
```

Expected: FAIL — `assert isinstance(..., RotationTickerSync)` fails because `rotation_ticker_sync` is still `None`.

- [ ] **Step 2: Wire the factory**

In `kj-controller/app.py`, immediately after the `PushDispatcher` block (around line 247, after `flask_app.rotation.push_dispatcher = PushDispatcher(...)`), add:

```python
    # ----------------------------------------------------------------
    # RotationTickerSync — keeps rotation-driven ticker overlays in sync
    # ----------------------------------------------------------------
    from rotation_ticker_sync import RotationTickerSync

    flask_app.rotation.rotation_ticker_sync = RotationTickerSync(
        overlay_manager=flask_app.overlay_manager,
        rotation_store=flask_app.rotation.store,
    )
    # Populate any rotation tickers that already exist in the saved config.
    try:
        flask_app.rotation.rotation_ticker_sync.refresh()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("initial rotation ticker refresh failed")
```

- [ ] **Step 3: Run the full hook test file**

```bash
pytest tests/integration/test_rotation_ticker_hook.py -v
```

Expected: 4 passed.

- [ ] **Step 4: Smoke the rest of the suite to confirm no regressions in factory wiring**

```bash
pytest tests/ -x -q
```

Expected: all green (skip e2e if they require devices — pytest will mark/skip per project conventions).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/app.py
git commit -m "feat(rotation): wire RotationTickerSync in create_app + initial refresh"
```

---

## Task 7: `POST /overlays/presets/<name>` route + refresh hook in POST/PUT

**Files:**
- Modify: `kj-controller/routes.py`
- Create: `kj-controller/tests/integration/test_overlay_presets_route.py`

- [ ] **Step 1: Write failing route tests**

Create `kj-controller/tests/integration/test_overlay_presets_route.py`:

```python
"""Integration tests for /overlays/presets/<name> and ticker refresh on save."""

import pytest


class TestPresetRoute:
    def test_scan_to_sing_creates_qr_overlay(self, flask_test_client):
        resp = flask_test_client.post('/overlays/presets/scan-to-sing')
        assert resp.status_code == 201
        overlay = resp.get_json()
        assert overlay['type'] == 'qr_code'
        assert overlay['name'] == 'Scan to Sing'
        assert overlay['show_over_video'] is True
        assert overlay['enabled'] is True
        cfg = overlay['config']
        assert cfg['follow_event_url'] is True
        # url must be populated by sync_event_url_overlays
        assert cfg['url']

    def test_scan_to_sing_persists_in_listing(self, flask_test_client):
        resp = flask_test_client.post('/overlays/presets/scan-to-sing')
        created_id = resp.get_json()['id']
        listing = flask_test_client.get('/overlays').get_json()
        assert any(o['id'] == created_id for o in listing)

    def test_unknown_preset_returns_400(self, flask_test_client):
        resp = flask_test_client.post('/overlays/presets/does-not-exist')
        assert resp.status_code == 400


class TestTickerRefreshOnSave:
    def test_create_rotation_ticker_populates_text(self, flask_test_client, sing_app):
        sing_app.rotation.add_entry('Alice')
        resp = flask_test_client.post('/overlays', json={
            'type': 'ticker',
            'name': 'Rotation Bar',
            'enabled': True,
            'show_over_video': True,
            'config': {
                'source': 'rotation',
                'prefix': 'Up next: ',
                'count': 5,
                'separator': '   ',
                'empty_text': 'Sign up at the booth!',
                'position': 'top',
            },
        })
        assert resp.status_code == 201
        oid = resp.get_json()['id']

        # After the post-save refresh, config.text should be populated
        overlay = sing_app.overlay_manager.get_overlay(oid)
        assert overlay['config']['text'] == 'Up next: 1. Alice'

    def test_update_to_rotation_source_populates_text(self, flask_test_client, sing_app):
        sing_app.rotation.add_entry('Bob')
        create = flask_test_client.post('/overlays', json={
            'type': 'ticker',
            'name': 'Static',
            'config': {'text': 'placeholder', 'source': 'static'},
        })
        oid = create.get_json()['id']

        update = flask_test_client.put(f'/overlays/{oid}', json={
            'config': {
                'source': 'rotation',
                'prefix': 'Now: ',
                'count': 5,
                'separator': ' | ',
                'empty_text': '',
                'text': 'placeholder',
            },
        })
        assert update.status_code == 200
        assert sing_app.overlay_manager.get_overlay(oid)['config']['text'] == 'Now: 1. Bob'
```

- [ ] **Step 2: Run tests, verify failures**

```bash
pytest tests/integration/test_overlay_presets_route.py -v
```

Expected: all fail (route 404, no refresh hook).

- [ ] **Step 3: Add the route + hook the refresh into POST/PUT**

In `kj-controller/routes.py`, at the top of the imports block, add (if not already present):

```python
from sing import sync_event_url_overlays, get_event_url
```

Add a new route handler near the other overlay routes (after `import_overlays`):

```python
@routes_bp.route('/overlays/presets/<preset_name>', methods=['POST'])
def create_overlay_preset(preset_name):
    """Create a new overlay from a named preset."""
    try:
        overlay = current_app.overlay_manager.create_preset(preset_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Auto-fill the event URL for scan-to-sing (config.follow_event_url=True is set
    # by the preset; sync handles the actual url copy).
    if preset_name == 'scan-to-sing':
        try:
            url = _scan_to_sing_url()
            sync_event_url_overlays(current_app.overlay_manager, url)
            # Re-read so the response includes the populated url
            overlay = current_app.overlay_manager.get_overlay(overlay['id'])
        except Exception:
            import logging
            logging.getLogger(__name__).exception("scan-to-sing url sync failed")

    return jsonify(overlay), 201


def _scan_to_sing_url():
    """Compose the current public sing URL for the active token."""
    cfg = current_app.kj_config or {}
    token = current_app.sing_store.get_token()
    return get_event_url(cfg, token, scope='public')
```

Modify the existing `create_overlay` handler so it triggers a rotation ticker refresh after creating a rotation ticker:

```python
@routes_bp.route('/overlays', methods=['POST'])
def create_overlay():
    """Creates a new overlay."""
    data = request.get_json(silent=True)
    if not data or 'type' not in data:
        return jsonify({"error": "type is required"}), 400
    try:
        overlay = current_app.overlay_manager.create_overlay(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _maybe_refresh_rotation_ticker(overlay)
    overlay = current_app.overlay_manager.get_overlay(overlay['id'])
    return jsonify(overlay), 201
```

Modify the existing `update_overlay` handler similarly:

```python
@routes_bp.route('/overlays/<overlay_id>', methods=['PUT'])
def update_overlay(overlay_id):
    """Updates an existing overlay."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body required"}), 400
    overlay = current_app.overlay_manager.update_overlay(overlay_id, data)
    if not overlay:
        return jsonify({"error": "Overlay not found"}), 404

    _maybe_refresh_rotation_ticker(overlay)
    overlay = current_app.overlay_manager.get_overlay(overlay_id)
    return jsonify(overlay)
```

Add the helper at module scope:

```python
def _maybe_refresh_rotation_ticker(overlay):
    """If the overlay is a rotation-driven ticker, trigger an immediate refresh."""
    if (overlay or {}).get('type') != 'ticker':
        return
    cfg = (overlay.get('config') or {})
    if cfg.get('source') != 'rotation':
        return
    sync = getattr(current_app.rotation, 'rotation_ticker_sync', None)
    if sync is None:
        return
    try:
        sync.refresh()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("rotation ticker refresh on save failed")
```

- [ ] **Step 4: Run the new tests, verify PASS**

```bash
pytest tests/integration/test_overlay_presets_route.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the broader overlay route tests to confirm no regressions**

```bash
pytest tests/integration/test_overlay_routes.py -v
```

Expected: all existing pass.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_overlay_presets_route.py
git commit -m "feat(routes): /overlays/presets/<name> + refresh rotation ticker on save"
```

---

## Task 8: Engine — QR `bg_opacity` and `corner_radius`

**Files:**
- Modify: `desktop/overlay_types.py`
- Create: `kj-controller/tests/unit/test_qr_overlay_visual.py`

We extract two pure helpers we can unit-test, then call them from `QRCodeOverlay._setup()` / `render()`. Pygame surface rendering itself isn't unit-tested.

- [ ] **Step 1: Write the failing test**

Create `kj-controller/tests/unit/test_qr_overlay_visual.py`:

```python
"""Unit tests for QR overlay visual helpers (color blending, rounded mask).

These cover the math + branching; actual pygame rendering is verified by
manual smoke test on the device (see plan, Task 12).
"""

import sys
import os

# Engine code lives in desktop/ — import from there
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'desktop'))

import pytest


class TestPremultiplyBg:
    def test_fully_opaque_returns_original_color(self):
        from overlay_types import _make_bg_color
        assert _make_bg_color('#102030', 1.0) == (16, 32, 48)

    def test_zero_opacity_returns_black(self):
        from overlay_types import _make_bg_color
        assert _make_bg_color('#FFFFFF', 0.0) == (0, 0, 0)

    def test_half_opacity_halves_components(self):
        from overlay_types import _make_bg_color
        # bg_opacity=0.5 on white → (127, 127, 127)
        r, g, b = _make_bg_color('#FFFFFF', 0.5)
        assert (r, g, b) == (127, 127, 127)


class TestBuildRoundedMask:
    def test_corner_radius_zero_returns_none(self):
        from overlay_types import _build_rounded_mask
        assert _build_rounded_mask(width=100, height=80, radius=0) is None

    def test_corner_radius_positive_returns_mask_with_correct_size(self):
        from overlay_types import _build_rounded_mask
        mask = _build_rounded_mask(width=200, height=150, radius=12)
        assert mask is not None
        assert mask.size == (200, 150)
        assert mask.mode == 'L'  # 8-bit alpha mask

    def test_corner_radius_clamped_to_half_min_dimension(self):
        """A radius larger than min(width,height)/2 must be clamped, not crash."""
        from overlay_types import _build_rounded_mask
        mask = _build_rounded_mask(width=40, height=20, radius=999)
        assert mask is not None
        assert mask.size == (40, 20)
```

- [ ] **Step 2: Run tests, verify failures**

```bash
pytest tests/unit/test_qr_overlay_visual.py -v
```

Expected: `ImportError` for `_build_rounded_mask` (the function doesn't exist yet). `_make_bg_color` exists already so its tests should pass — confirm.

- [ ] **Step 3: Add `_build_rounded_mask` and use both helpers in `QRCodeOverlay`**

In `desktop/overlay_types.py`, after `_make_bg_color`, add:

```python
def _build_rounded_mask(width, height, radius):
    """Return an 8-bit alpha mask (PIL Image, mode='L') for a rounded rectangle.

    Returns None when radius<=0 (caller should skip the mask step).
    """
    if not _pil_available or radius <= 0 or width <= 0 or height <= 0:
        return None
    radius = min(int(radius), min(width, height) // 2)
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)
    try:
        draw.rounded_rectangle([(0, 0), (width - 1, height - 1)], radius=radius, fill=255)
    except (AttributeError, TypeError):
        # Older Pillow without rounded_rectangle — fall back to plain rect
        draw.rectangle([(0, 0), (width - 1, height - 1)], fill=255)
    return mask
```

In `QRCodeOverlay._setup`, replace the existing setup with one that captures the new fields:

```python
    def _setup(self):
        size = self.config.get('size', 180)
        padding = self.config.get('padding', 10)
        url = self.config.get('url', '')
        label = self.config.get('label', '')

        self._qr_surface = self._generate_qr(url, size)
        self._cached_url = url

        label_height = 0
        if label:
            font = _get_font(max(12, size // 10))
            self._label_surface = font.render(label, True, (255, 255, 255))
            label_height = self._label_surface.get_height() + 4
        else:
            self._label_surface = None

        self._width = size + padding * 2
        self._height = size + label_height + padding * 2
        self._padding = padding
        self._qr_size = size
        self._label_height = label_height

        position = self.config.get('position', 'bottom-right')
        custom_x = self.config.get('custom_x')
        custom_y = self.config.get('custom_y')
        self._x, self._y = calculate_position(position, self._width, self._height, custom_x, custom_y)

        self._bg_color = _make_bg_color(
            self.config.get('bg_color', '#000000'),
            self.config.get('bg_opacity', 1.0),
        )
        self._rounded_mask = _build_rounded_mask(
            self._width, self._height,
            int(self.config.get('corner_radius', 0) or 0),
        )
```

Modify `QRCodeOverlay.render` to apply the rounded mask when present:

```python
    def render(self):
        if not self.window or not self.surface:
            return

        if self._rounded_mask is None:
            self.surface.fill(self._bg_color)
        else:
            # Build the full overlay card in PIL, then blit to pygame as one surface
            r, g, b = self._bg_color
            card = Image.new('RGBA', (self._width, self._height), (r, g, b, 255))
            card.putalpha(self._rounded_mask)
            raw = card.tobytes()
            bg_surf = pygame.image.frombytes(raw, card.size, 'RGBA')
            self.surface.fill((0, 0, 0))
            self.surface.blit(bg_surf, (0, 0))

        if self._qr_surface:
            self.surface.blit(self._qr_surface, (self._padding, self._padding))

        if self._label_surface:
            label_x = (self._width - self._label_surface.get_width()) // 2
            label_y = self._padding + self._qr_size + 4
            self.surface.blit(self._label_surface, (label_x, label_y))

        self.window.flip()
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
pytest tests/unit/test_qr_overlay_visual.py -v
```

Expected: 6 passed (3 for `_make_bg_color`, 3 for `_build_rounded_mask`).

- [ ] **Step 5: Commit**

```bash
git add desktop/overlay_types.py kj-controller/tests/unit/test_qr_overlay_visual.py
git commit -m "feat(overlay): QR overlay bg_opacity + corner_radius for video overlay"
```

---

## Task 9: Engine — Z-order pass for QR over ticker

**Files:**
- Modify: `desktop/overlay_engine.py`
- Create: `kj-controller/tests/unit/test_engine_restack.py`

- [ ] **Step 1: Write the failing test**

Create `kj-controller/tests/unit/test_engine_restack.py`:

```python
"""Unit tests for the QR-above-ticker Z-order restack logic."""

import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'desktop'))


def _ticker_overlay(visible=True):
    from overlay_types import TickerOverlay
    ov = MagicMock(spec=TickerOverlay)
    ov.visible = visible
    return ov


def _qr_overlay(visible=True):
    from overlay_types import QRCodeOverlay
    ov = MagicMock(spec=QRCodeOverlay)
    ov.visible = visible
    return ov


class TestRestackQROverTicker:
    def test_no_qr_overlays_is_noop(self):
        from overlay_engine import OverlayEngine
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'t': _ticker_overlay(visible=True)}
        eng._restack_qr_above_ticker()
        # Nothing to do; method must not blow up

    def test_qr_without_ticker_is_noop(self):
        from overlay_engine import OverlayEngine
        qr = _qr_overlay(visible=True)
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'q': qr}
        eng._restack_qr_above_ticker()
        qr.destroy_window.assert_not_called()
        qr.create_window.assert_not_called()

    def test_qr_with_visible_ticker_restacks_qr_only(self):
        from overlay_engine import OverlayEngine
        t = _ticker_overlay(visible=True)
        q = _qr_overlay(visible=True)
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'t': t, 'q': q}
        eng._restack_qr_above_ticker()
        q.destroy_window.assert_called_once()
        q.create_window.assert_called_once()
        q.render.assert_called_once()
        # Ticker is untouched
        t.destroy_window.assert_not_called()
        t.create_window.assert_not_called()

    def test_invisible_qr_is_skipped(self):
        from overlay_engine import OverlayEngine
        t = _ticker_overlay(visible=True)
        q = _qr_overlay(visible=False)
        eng = OverlayEngine.__new__(OverlayEngine)
        eng.overlays = {'t': t, 'q': q}
        eng._restack_qr_above_ticker()
        q.destroy_window.assert_not_called()
```

- [ ] **Step 2: Run tests, verify failures**

```bash
pytest tests/unit/test_engine_restack.py -v
```

Expected: 4 failures — `_restack_qr_above_ticker` not implemented.

- [ ] **Step 3: Implement the restack pass**

In `desktop/overlay_engine.py`, add `import` for the two classes at the top if not already there:

```python
from overlay_types import QRCodeOverlay, TickerOverlay, create_overlay
```

(The existing module already imports `create_overlay`; just extend the import line.)

Add the method to `OverlayEngine` (place after `update_visibility`):

```python
    def _restack_qr_above_ticker(self):
        """Ensure QR overlays are mapped on top of any visible ticker by
        destroying and re-creating their windows. X11 maps new windows on top
        of existing always-on-top peers, which is the cheapest deterministic
        fix for stacking-order issues between two always_on_top windows.
        """
        qr_overlays = [
            ov for ov in self.overlays.values()
            if isinstance(ov, QRCodeOverlay) and ov.visible
        ]
        if not qr_overlays:
            return
        has_visible_ticker = any(
            isinstance(ov, TickerOverlay) and ov.visible
            for ov in self.overlays.values()
        )
        if not has_visible_ticker:
            return
        for ov in qr_overlays:
            ov.destroy_window()
            ov.create_window()
            ov.render()
```

Call it from `_reload_config()` at the end, and from `update_visibility()` after the visibility pass:

```python
    def _reload_config(self):
        # ...existing body unchanged...
        self._restack_qr_above_ticker()

    def update_visibility(self):
        # ...existing body unchanged...
        self._restack_qr_above_ticker()
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
pytest tests/unit/test_engine_restack.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Smoke the existing engine tests**

```bash
pytest tests/unit/test_overlay_engine.py -v
```

Expected: all existing pass (the restack call is a no-op when there are no QR overlays in those tests).

- [ ] **Step 6: Commit**

```bash
git add desktop/overlay_engine.py kj-controller/tests/unit/test_engine_restack.py
git commit -m "feat(overlay-engine): restack QR above visible tickers"
```

---

## Task 10: Frontend — "Scan to Sing" button

**Files:**
- Modify: `kj-controller/templates/index.html`
- Modify: `kj-controller/static/app.js`

This task is UI-only and not directly unit-tested in Python; verification is via the existing integration tests for the POST route plus a manual UI smoke step at the end.

- [ ] **Step 1: Add the button**

In `kj-controller/templates/index.html`, find the existing overlay-panel header (around line 109–117). Locate the row with the `Wallpaper`, `Backup`, `Restore`, `+ Add` buttons. Insert a new button just **before** `+ Add`:

```html
                        <button class="overlay-header-btn" onclick="addScanToSingQR()" title="Add a small QR code in the top-right for singers to scan">Scan to Sing</button>
```

- [ ] **Step 2: Add the JS handler**

In `kj-controller/static/app.js`, near the other overlay helpers (e.g. close to `backupOverlays`), add:

```javascript
async function addScanToSingQR() {
    try {
        const resp = await fetch('/overlays/presets/scan-to-sing', { method: 'POST' });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(`Failed to add Scan-to-Sing QR: ${err.error || resp.statusText}`);
            return;
        }
        await loadOverlays();
    } catch (e) {
        alert(`Failed to add Scan-to-Sing QR: ${e.message || e}`);
    }
}
```

> `loadOverlays()` is defined at `kj-controller/static/app.js:1790` and is the existing helper that re-fetches and re-renders the overlay list.

- [ ] **Step 3: Verify integration test still passes**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-overlays-ticker-qr/kj-controller
pytest tests/integration/test_overlay_presets_route.py -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add kj-controller/templates/index.html kj-controller/static/app.js
git commit -m "feat(ui): Scan-to-Sing button creates preset QR overlay"
```

---

## Task 11: Frontend — Ticker Source select + conditional rotation fields

**Files:**
- Modify: `kj-controller/templates/index.html`
- Modify: `kj-controller/static/app.js`

- [ ] **Step 1: Add the Source select + rotation fields to the overlay modal**

In `kj-controller/templates/index.html`, inside the overlay-modal (around line 426), **before** the existing ticker text textarea row (`data-types="ticker,static_text"`), insert:

```html
                <div class="overlay-form-row overlay-field" data-types="ticker">
                    <label for="overlay-source">Source</label>
                    <select id="overlay-source" onchange="onOverlayTickerSourceChange()">
                        <option value="static">Static text</option>
                        <option value="rotation">Rotation: upcoming singers</option>
                    </select>
                </div>
```

After the existing text textarea row, add the four rotation-only field rows:

```html
                <div class="overlay-form-row overlay-field" data-types="ticker" data-source="rotation">
                    <label for="overlay-prefix">Prefix</label>
                    <input type="text" id="overlay-prefix" value="Up next: " placeholder="Up next: ">
                </div>
                <div class="overlay-form-row overlay-field" data-types="ticker" data-source="rotation">
                    <label for="overlay-count">Show next N singers</label>
                    <input type="number" id="overlay-count" min="1" max="20" value="5">
                </div>
                <div class="overlay-form-row overlay-field" data-types="ticker" data-source="rotation">
                    <label for="overlay-separator">Separator</label>
                    <input type="text" id="overlay-separator" value="   " placeholder="   ">
                </div>
                <div class="overlay-form-row overlay-field" data-types="ticker" data-source="rotation">
                    <label for="overlay-empty-text">When rotation is empty</label>
                    <input type="text" id="overlay-empty-text" value="Sign up at the booth!" placeholder="Sign up at the booth!">
                </div>
```

- [ ] **Step 2: Update `onOverlayTypeChange` + add `onOverlayTickerSourceChange`**

In `kj-controller/static/app.js`, replace the existing `onOverlayTypeChange` function:

```javascript
function onOverlayTypeChange() {
    const type = document.getElementById('overlay-type').value;
    const source = document.getElementById('overlay-source')?.value || 'static';
    document.querySelectorAll('.overlay-field').forEach(el => {
        const types = el.dataset.types.split(',');
        const sourceFilter = el.dataset.source;
        let visible = types.includes(type);
        if (visible && sourceFilter) {
            visible = (type === 'ticker' && source === sourceFilter);
        }
        el.classList.toggle('hidden', !visible);
    });
    // Set sensible default position based on type (and source for ticker)
    const posSelect = document.getElementById('overlay-position');
    if (type === 'ticker') {
        posSelect.value = (source === 'rotation') ? 'top' : 'bottom';
    } else if (type === 'qr_code') {
        posSelect.value = 'bottom-right';
    } else if (type === 'countdown') {
        posSelect.value = 'top-center';
    } else if (type === 'static_text') {
        posSelect.value = 'top-right';
    } else if (type === 'image') {
        posSelect.value = 'top-right';
    }
}

function onOverlayTickerSourceChange() {
    onOverlayTypeChange();
}
```

- [ ] **Step 3: Update `buildOverlayConfig()` to include the new ticker fields**

In `kj-controller/static/app.js`, the config-building function lives at `buildOverlayConfig()` (~line 1954). Replace the existing two ticker branches:

```javascript
    if (type === 'ticker' || type === 'static_text') {
        config.text = document.getElementById('overlay-text').value;
    }
    if (type === 'ticker') {
        config.speed = parseFloat(document.getElementById('overlay-speed').value);
    }
```

with the source-aware version:

```javascript
    if (type === 'static_text') {
        config.text = document.getElementById('overlay-text').value;
    }
    if (type === 'ticker') {
        const source = document.getElementById('overlay-source').value;
        config.source = source;
        config.speed = parseFloat(document.getElementById('overlay-speed').value);
        if (source === 'rotation') {
            config.prefix = document.getElementById('overlay-prefix').value;
            config.count = parseInt(document.getElementById('overlay-count').value, 10) || 5;
            config.separator = document.getElementById('overlay-separator').value;
            config.empty_text = document.getElementById('overlay-empty-text').value;
            // text is derived by the backend on save (rotation_ticker_sync.refresh)
            config.text = '';
        } else {
            config.text = document.getElementById('overlay-text').value;
        }
    }
```

- [ ] **Step 4: Populate the new fields in `showOverlayForm()`**

Find `showOverlayForm` (~line 1900) and inside the `if (overlay) {…}` block where existing ticker fields are read from `overlay.config`, add (after the existing text/speed population):

```javascript
        document.getElementById('overlay-source').value = overlay.config.source || 'static';
        document.getElementById('overlay-prefix').value = overlay.config.prefix ?? 'Up next: ';
        document.getElementById('overlay-count').value = overlay.config.count ?? 5;
        document.getElementById('overlay-separator').value = overlay.config.separator ?? '   ';
        document.getElementById('overlay-empty-text').value = overlay.config.empty_text ?? 'Sign up at the booth!';
```

And in the `else` branch (new overlay) reset the defaults:

```javascript
        document.getElementById('overlay-source').value = 'static';
        document.getElementById('overlay-prefix').value = 'Up next: ';
        document.getElementById('overlay-count').value = 5;
        document.getElementById('overlay-separator').value = '   ';
        document.getElementById('overlay-empty-text').value = 'Sign up at the booth!';
```

Then ensure `onOverlayTypeChange()` is called (it already is at the bottom of `showOverlayForm`) so visibility flips correctly.

- [ ] **Step 5: Confirm the backend round-trip works via the existing tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-overlays-ticker-qr/kj-controller
pytest tests/integration/test_overlay_presets_route.py tests/integration/test_overlay_routes.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/templates/index.html kj-controller/static/app.js
git commit -m "feat(ui): ticker Source select + rotation-driven ticker fields"
```

---

## Task 12: Full test sweep + manual smoke checklist

- [ ] **Step 1: Run the entire kj-controller test suite**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-overlays-ticker-qr/kj-controller
pytest -q
```

Expected: all green. If any e2e tests fail because of device-specific requirements, mark per existing project conventions and confirm with the user before proceeding.

- [ ] **Step 2: Sanity-check the suite covers what we built**

```bash
pytest -q -k "ticker or preset or qr or rotation_ticker_sync or restack or compose_ticker"
```

Expected: substantial test count, all green.

- [ ] **Step 3: Manual smoke checklist (record results in PR description, do not deploy yet)**

These steps require local Python + dev pygame; on NomadPC they require an SSH session. Do NOT push or restart kj-controller until the user explicitly authorises (per CLAUDE.md production safety).

Local smoke (no device required):
- Launch the engine in demo mode: `python3 desktop/overlay_engine.py --demo`. Confirm ticker, QR, countdown, static text show as before.
- Hand-edit `/tmp/overlay-demo-*/overlays.json` to add a `corner_radius: 12` and `bg_opacity: 0.5` to the QR. Confirm rounded card + see-through padding.
- Hand-edit the demo ticker to `"source": "rotation"`. Confirm engine doesn't crash (text falls back to whatever was in `text`).

Backend smoke (via test client, no device required):
- Spin up Flask app via `tests` fixtures or `python3 -m kj-controller.app --dev` (whichever the project supports).
- `curl -X POST http://localhost:80/overlays/presets/scan-to-sing` returns 201 with a populated URL.
- POST a rotation ticker; add a singer via `/rotation`; GET the overlay; confirm `config.text` is `Up next: 1. <singer>`.

Engine smoke on NomadPC (REQUIRES USER PERMISSION before any push):
- After user approves: push to branch, deploy, restart `overlay-display.service`, watch `journalctl -u overlay-display -f`.
- Confirm QR sits visually above ticker at the overlap point.

- [ ] **Step 4: Update `docs/CHANGELOG.md`**

Add a dated entry under the changelog file describing the new overlay capabilities:

```markdown
## 2026-05-28 — Overlay system: rotation ticker + Scan-to-Sing preset

- Ticker overlays gained a `source: rotation` mode whose text is composed by
  the backend on every rotation mutation. New `prefix`, `count`, `separator`,
  `empty_text` config fields.
- New `POST /overlays/presets/scan-to-sing` plus a "Scan to Sing" button in
  the overlay panel creates a small QR overlay (top-right, follow_event_url)
  ready to scan.
- QR overlays gained `bg_opacity` (semi-transparent padding) and
  `corner_radius` (rounded card) for better video/ticker overlay.
- Overlay engine restacks QR windows above any visible ticker so the QR
  reliably sits on top.
```

- [ ] **Step 5: Commit the changelog**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: changelog for rotation ticker + Scan-to-Sing overlay improvements"
```
