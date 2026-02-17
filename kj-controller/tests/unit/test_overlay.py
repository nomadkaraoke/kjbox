"""Unit tests for OverlayManager and overlay config."""

import json
import os

import pytest

from overlay import OverlayManager


@pytest.fixture
def overlays_path(tmp_path):
    """Temporary overlays.json path."""
    return str(tmp_path / 'overlays.json')


@pytest.fixture
def manager(overlays_path):
    """OverlayManager with a temp config file."""
    return OverlayManager(config_path=overlays_path)


class TestOverlayManagerCRUD:
    """Test create, read, update, delete operations."""

    def test_initial_state_empty(self, manager):
        assert manager.list_overlays() == []
        assert manager.karaoke_playing is False

    def test_create_ticker(self, manager):
        overlay = manager.create_overlay({
            'type': 'ticker',
            'name': 'Test Ticker',
            'enabled': True,
            'config': {'text': 'Hello World', 'speed': 2},
        })
        assert overlay['id']
        assert overlay['type'] == 'ticker'
        assert overlay['name'] == 'Test Ticker'
        assert overlay['enabled'] is True
        assert overlay['config']['text'] == 'Hello World'
        assert len(manager.list_overlays()) == 1

    def test_create_all_types(self, manager):
        for overlay_type in ('ticker', 'static_text', 'image', 'countdown', 'qr_code'):
            overlay = manager.create_overlay({
                'type': overlay_type,
                'name': f'Test {overlay_type}',
                'config': {},
            })
            assert overlay['type'] == overlay_type
        assert len(manager.list_overlays()) == 5

    def test_create_invalid_type_raises(self, manager):
        with pytest.raises(ValueError, match='Invalid overlay type'):
            manager.create_overlay({'type': 'invalid_type'})

    def test_get_overlay(self, manager):
        created = manager.create_overlay({
            'type': 'static_text',
            'name': 'Find Me',
            'config': {'text': 'test'},
        })
        found = manager.get_overlay(created['id'])
        assert found is not None
        assert found['name'] == 'Find Me'

    def test_get_overlay_not_found(self, manager):
        assert manager.get_overlay('nonexistent') is None

    def test_update_overlay(self, manager):
        created = manager.create_overlay({
            'type': 'ticker',
            'name': 'Before',
            'config': {'text': 'old'},
        })
        updated = manager.update_overlay(created['id'], {
            'name': 'After',
            'config': {'text': 'new', 'speed': 3},
        })
        assert updated['name'] == 'After'
        assert updated['config']['text'] == 'new'

    def test_update_nonexistent_returns_none(self, manager):
        assert manager.update_overlay('nope', {'name': 'X'}) is None

    def test_delete_overlay(self, manager):
        created = manager.create_overlay({
            'type': 'ticker',
            'name': 'Delete Me',
            'config': {},
        })
        assert manager.delete_overlay(created['id']) is True
        assert len(manager.list_overlays()) == 0

    def test_delete_nonexistent(self, manager):
        assert manager.delete_overlay('nope') is False

    def test_toggle_enabled(self, manager):
        created = manager.create_overlay({
            'type': 'ticker',
            'name': 'Toggle',
            'enabled': False,
            'config': {},
        })
        toggled = manager.toggle_enabled(created['id'])
        assert toggled['enabled'] is True
        toggled = manager.toggle_enabled(created['id'])
        assert toggled['enabled'] is False

    def test_toggle_show_over_video(self, manager):
        created = manager.create_overlay({
            'type': 'static_text',
            'name': 'Video Toggle',
            'show_over_video': False,
            'config': {},
        })
        toggled = manager.toggle_show_over_video(created['id'])
        assert toggled['show_over_video'] is True

    def test_toggle_nonexistent_returns_none(self, manager):
        assert manager.toggle_enabled('nope') is None
        assert manager.toggle_show_over_video('nope') is None


class TestOverlayManagerPersistence:
    """Test that overlays persist to disk."""

    def test_save_and_reload(self, overlays_path):
        mgr1 = OverlayManager(config_path=overlays_path)
        mgr1.create_overlay({
            'type': 'ticker',
            'name': 'Persistent',
            'config': {'text': 'Saved'},
        })

        # Create new manager from same file
        mgr2 = OverlayManager(config_path=overlays_path)
        overlays = mgr2.list_overlays()
        assert len(overlays) == 1
        assert overlays[0]['name'] == 'Persistent'

    def test_karaoke_playing_state_persists(self, overlays_path):
        mgr1 = OverlayManager(config_path=overlays_path)
        mgr1.set_karaoke_playing(True)

        mgr2 = OverlayManager(config_path=overlays_path)
        assert mgr2.karaoke_playing is True

    def test_atomic_write(self, overlays_path):
        manager = OverlayManager(config_path=overlays_path)
        manager.create_overlay({'type': 'ticker', 'config': {}})
        # File should be valid JSON
        with open(overlays_path) as f:
            data = json.load(f)
        assert 'overlays' in data
        assert len(data['overlays']) == 1

    def test_handles_missing_file(self, tmp_path):
        path = str(tmp_path / 'nonexistent' / 'overlays.json')
        mgr = OverlayManager(config_path=path)
        assert mgr.list_overlays() == []
        # Creating an overlay should create the file and directories
        mgr.create_overlay({'type': 'ticker', 'config': {}})
        assert os.path.exists(path)

    def test_handles_corrupt_file(self, overlays_path):
        with open(overlays_path, 'w') as f:
            f.write('not valid json{{{')
        mgr = OverlayManager(config_path=overlays_path)
        assert mgr.list_overlays() == []


class TestOverlayManagerKaraokeState:
    """Test karaoke_playing state management."""

    def test_set_karaoke_playing(self, manager, overlays_path):
        manager.set_karaoke_playing(True)
        assert manager.karaoke_playing is True
        # Verify written to file
        with open(overlays_path) as f:
            data = json.load(f)
        assert data['karaoke_playing'] is True

    def test_set_karaoke_playing_idempotent(self, manager, overlays_path):
        # First set to True (creates file)
        manager.set_karaoke_playing(True)
        mtime1 = os.path.getmtime(overlays_path)
        # Set to True again — should not write
        manager.set_karaoke_playing(True)
        mtime2 = os.path.getmtime(overlays_path)
        assert mtime1 == mtime2


class TestOverlayDefaults:
    """Test that overlays get proper defaults."""

    def test_default_enabled_false(self, manager):
        overlay = manager.create_overlay({'type': 'ticker', 'config': {}})
        assert overlay['enabled'] is False

    def test_default_show_over_video_false(self, manager):
        overlay = manager.create_overlay({'type': 'ticker', 'config': {}})
        assert overlay['show_over_video'] is False

    def test_default_name_empty(self, manager):
        overlay = manager.create_overlay({'type': 'ticker', 'config': {}})
        assert overlay['name'] == ''

    def test_id_generated(self, manager):
        o1 = manager.create_overlay({'type': 'ticker', 'config': {}})
        o2 = manager.create_overlay({'type': 'ticker', 'config': {}})
        assert o1['id'] != o2['id']
        assert len(o1['id']) == 8
