"""Unit tests for OverlayEngine config loading, sync, and visibility logic."""

import json
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

# Add desktop/ to path so overlay_engine can import overlay_config/overlay_types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'desktop'))


class TestOverlayEngineLoadConfig:
    """Tests for OverlayEngine.load_config()."""

    def test_load_valid_config(self, tmp_path):
        from overlay_engine import OverlayEngine

        config = {
            'karaoke_playing': True,
            'overlays': [
                {'id': 'o1', 'type': 'ticker', 'enabled': True, 'config': {'text': 'hi'}},
            ],
        }
        config_path = tmp_path / 'overlays.json'
        config_path.write_text(json.dumps(config))

        engine = OverlayEngine(config_path=str(config_path))
        playing, overlays = engine.load_config()
        assert playing is True
        assert len(overlays) == 1
        assert overlays[0]['id'] == 'o1'

    def test_load_missing_file_returns_defaults(self, tmp_path):
        from overlay_engine import OverlayEngine

        engine = OverlayEngine(config_path=str(tmp_path / 'nonexistent.json'))
        playing, overlays = engine.load_config()
        assert playing is False
        assert overlays == []

    def test_load_invalid_json_returns_defaults(self, tmp_path):
        from overlay_engine import OverlayEngine

        config_path = tmp_path / 'overlays.json'
        config_path.write_text('not valid json{{{')

        engine = OverlayEngine(config_path=str(config_path))
        playing, overlays = engine.load_config()
        assert playing is False
        assert overlays == []

    def test_load_empty_overlays(self, tmp_path):
        from overlay_engine import OverlayEngine

        config_path = tmp_path / 'overlays.json'
        config_path.write_text(json.dumps({'karaoke_playing': False, 'overlays': []}))

        engine = OverlayEngine(config_path=str(config_path))
        playing, overlays = engine.load_config()
        assert playing is False
        assert overlays == []

    def test_load_applies_defaults(self, tmp_path):
        from overlay_engine import OverlayEngine

        config = {
            'karaoke_playing': False,
            'overlays': [
                {'id': 'o1', 'type': 'ticker', 'enabled': True, 'config': {}},
            ],
        }
        config_path = tmp_path / 'overlays.json'
        config_path.write_text(json.dumps(config))

        engine = OverlayEngine(config_path=str(config_path))
        _, overlays = engine.load_config()
        # apply_defaults should have filled in default ticker config
        assert 'text_color' in overlays[0]['config']


class TestOverlayEngineReloadConfig:
    """Tests for OverlayEngine._reload_config() sync logic."""

    def _make_engine(self, tmp_path, config_data):
        config_path = tmp_path / 'overlays.json'
        config_path.write_text(json.dumps(config_data))
        from overlay_engine import OverlayEngine
        engine = OverlayEngine(config_path=str(config_path))
        return engine, config_path

    @patch('overlay_engine.create_overlay')
    def test_creates_new_enabled_overlay(self, mock_create, tmp_path):
        config = {
            'karaoke_playing': False,
            'overlays': [
                {'id': 'o1', 'type': 'ticker', 'enabled': True, 'config': {'text': 'hi'}},
            ],
        }
        engine, _ = self._make_engine(tmp_path, config)

        mock_overlay = MagicMock()
        mock_create.return_value = mock_overlay

        engine._reload_config()

        mock_create.assert_called_once()
        mock_overlay.create_window.assert_called_once()
        mock_overlay.render.assert_called_once()
        assert 'o1' in engine.overlays

    @patch('overlay_engine.create_overlay')
    def test_skips_disabled_overlay(self, mock_create, tmp_path):
        config = {
            'karaoke_playing': False,
            'overlays': [
                {'id': 'o1', 'type': 'ticker', 'enabled': False, 'config': {}},
            ],
        }
        engine, _ = self._make_engine(tmp_path, config)
        engine._reload_config()

        mock_create.assert_not_called()
        assert 'o1' not in engine.overlays

    @patch('overlay_engine.create_overlay')
    def test_removes_overlay_no_longer_in_config(self, mock_create, tmp_path):
        config = {
            'karaoke_playing': False,
            'overlays': [
                {'id': 'o1', 'type': 'ticker', 'enabled': True, 'config': {'text': 'hi'}},
            ],
        }
        engine, config_path = self._make_engine(tmp_path, config)

        mock_overlay = MagicMock()
        mock_create.return_value = mock_overlay
        engine._reload_config()
        assert 'o1' in engine.overlays

        # Remove the overlay from config and reload
        config_path.write_text(json.dumps({'karaoke_playing': False, 'overlays': []}))
        engine._reload_config()

        mock_overlay.cleanup.assert_called_once()
        assert 'o1' not in engine.overlays

    @patch('overlay_engine.create_overlay')
    def test_removes_overlay_when_disabled(self, mock_create, tmp_path):
        config = {
            'karaoke_playing': False,
            'overlays': [
                {'id': 'o1', 'type': 'ticker', 'enabled': True, 'config': {'text': 'hi'}},
            ],
        }
        engine, config_path = self._make_engine(tmp_path, config)

        mock_overlay = MagicMock()
        mock_create.return_value = mock_overlay
        engine._reload_config()

        # Disable the overlay
        config['overlays'][0]['enabled'] = False
        config_path.write_text(json.dumps(config))
        engine._reload_config()

        mock_overlay.cleanup.assert_called_once()
        assert 'o1' not in engine.overlays

    @patch('overlay_engine.create_overlay')
    def test_updates_existing_overlay(self, mock_create, tmp_path):
        config = {
            'karaoke_playing': False,
            'overlays': [
                {'id': 'o1', 'type': 'ticker', 'enabled': True, 'config': {'text': 'old'}},
            ],
        }
        engine, config_path = self._make_engine(tmp_path, config)

        mock_overlay = MagicMock()
        mock_create.return_value = mock_overlay
        engine._reload_config()

        # Update the overlay config
        config['overlays'][0]['config']['text'] = 'new'
        config_path.write_text(json.dumps(config))
        engine._reload_config()

        # Should update, not recreate
        mock_overlay.update_config.assert_called_once()
        assert mock_create.call_count == 1  # Only created once

    @patch('overlay_engine.create_overlay')
    def test_updates_karaoke_playing_state(self, mock_create, tmp_path):
        config = {'karaoke_playing': True, 'overlays': []}
        engine, _ = self._make_engine(tmp_path, config)
        engine._reload_config()
        assert engine.karaoke_playing is True


class TestOverlayEngineVisibility:
    """Tests for update_visibility() show/hide logic."""

    def test_hides_desktop_only_when_playing(self):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = True

        overlay = MagicMock()
        overlay.show_over_video = False
        overlay.visible = True
        engine.overlays = {'o1': overlay}

        engine.update_visibility()
        overlay.hide.assert_called_once()

    def test_shows_desktop_only_when_not_playing(self):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = False

        overlay = MagicMock()
        overlay.show_over_video = False
        overlay.visible = False
        engine.overlays = {'o1': overlay}

        engine.update_visibility()
        overlay.show.assert_called_once()

    def test_always_visible_shows_when_hidden(self):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = True

        overlay = MagicMock()
        overlay.show_over_video = True
        overlay.visible = False
        engine.overlays = {'o1': overlay}

        engine.update_visibility()
        overlay.show.assert_called_once()
        overlay.hide.assert_not_called()

    def test_always_visible_stays_shown(self):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = True

        overlay = MagicMock()
        overlay.show_over_video = True
        overlay.visible = True
        engine.overlays = {'o1': overlay}

        engine.update_visibility()
        overlay.show.assert_not_called()
        overlay.hide.assert_not_called()

    def test_desktop_only_stays_visible_when_not_playing(self):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = False

        overlay = MagicMock()
        overlay.show_over_video = False
        overlay.visible = True
        engine.overlays = {'o1': overlay}

        engine.update_visibility()
        overlay.show.assert_not_called()
        overlay.hide.assert_not_called()

    def test_multiple_overlays_mixed_visibility(self):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = True

        desktop_only = MagicMock()
        desktop_only.show_over_video = False
        desktop_only.visible = True

        always_on = MagicMock()
        always_on.show_over_video = True
        always_on.visible = True

        engine.overlays = {'d1': desktop_only, 'a1': always_on}
        engine.update_visibility()

        desktop_only.hide.assert_called_once()
        always_on.hide.assert_not_called()

    def test_no_restack_when_visibility_unchanged(self):
        """Steady state must not restack — restacking destroys+recreates QR
        windows, which flickers when run every frame (regression for QR flicker).
        """
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = True

        # Both already in their correct visibility state — nothing toggles.
        always_on = MagicMock()
        always_on.show_over_video = True
        always_on.visible = True
        desktop_only = MagicMock()
        desktop_only.show_over_video = False
        desktop_only.visible = False
        engine.overlays = {'a1': always_on, 'd1': desktop_only}

        with patch.object(engine, '_restack_qr_above_ticker') as mock_restack:
            engine.update_visibility()
            mock_restack.assert_not_called()

    def test_restacks_when_visibility_changes(self):
        """A visibility toggle must trigger exactly one restack to fix Z-order."""
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        engine.karaoke_playing = True

        # desktop-only overlay is visible and must be hidden → a change occurs.
        desktop_only = MagicMock()
        desktop_only.show_over_video = False
        desktop_only.visible = True
        engine.overlays = {'d1': desktop_only}

        with patch.object(engine, '_restack_qr_above_ticker') as mock_restack:
            engine.update_visibility()
            mock_restack.assert_called_once()


class TestOverlayEngineCheckConfig:
    """Tests for check_config() mtime polling logic."""

    def test_skips_check_within_poll_interval(self, tmp_path):
        from overlay_engine import OverlayEngine
        config_path = tmp_path / 'overlays.json'
        config_path.write_text(json.dumps({'karaoke_playing': False, 'overlays': []}))

        engine = OverlayEngine(config_path=str(config_path))
        engine._last_config_check = time.monotonic()  # Just checked

        with patch.object(engine, '_reload_config') as mock_reload:
            engine.check_config()
            mock_reload.assert_not_called()

    def test_reloads_on_mtime_change(self, tmp_path):
        from overlay_engine import OverlayEngine
        config_path = tmp_path / 'overlays.json'
        config_path.write_text(json.dumps({'karaoke_playing': False, 'overlays': []}))

        engine = OverlayEngine(config_path=str(config_path))
        engine._last_config_check = 0  # Force check

        with patch.object(engine, '_reload_config') as mock_reload:
            engine.check_config()
            mock_reload.assert_called_once()

    def test_no_reload_when_mtime_unchanged(self, tmp_path):
        from overlay_engine import OverlayEngine
        config_path = tmp_path / 'overlays.json'
        config_path.write_text(json.dumps({'karaoke_playing': False, 'overlays': []}))

        engine = OverlayEngine(config_path=str(config_path))
        engine._last_config_check = 0
        engine.check_config()  # First check — sets mtime

        engine._last_config_check = 0  # Force another check
        with patch.object(engine, '_reload_config') as mock_reload:
            engine.check_config()
            mock_reload.assert_not_called()  # Same mtime, no reload


class TestOverlayEngineShutdown:
    """Tests for shutdown()."""

    @patch('overlay_engine.pygame')
    def test_shutdown_cleans_all_overlays(self, mock_pygame):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()

        o1 = MagicMock()
        o2 = MagicMock()
        engine.overlays = {'o1': o1, 'o2': o2}

        engine.shutdown()
        o1.cleanup.assert_called_once()
        o2.cleanup.assert_called_once()
        assert len(engine.overlays) == 0
        mock_pygame.quit.assert_called_once()


class TestOverlayEngineSignal:
    """Tests for signal handling."""

    def test_handle_signal_stops_running(self):
        from overlay_engine import OverlayEngine
        engine = OverlayEngine()
        assert engine._running is True
        engine.handle_signal(15, None)  # SIGTERM
        assert engine._running is False
