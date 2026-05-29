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
