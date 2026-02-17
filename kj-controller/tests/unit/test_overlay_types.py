"""Unit tests for overlay_types: factory function, config changes, countdown logic."""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Add desktop/ to path so overlay_types is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'desktop'))

from overlay_config import hex_to_rgb


class TestCreateOverlayFactory:
    """Tests for the create_overlay() factory function."""

    @patch('overlay_types.pygame')
    def test_creates_ticker(self, mock_pygame):
        mock_pygame.font.Font.return_value = MagicMock(
            render=MagicMock(return_value=MagicMock(get_width=MagicMock(return_value=100)))
        )
        mock_pygame.font.SysFont.return_value = mock_pygame.font.Font.return_value
        from overlay_types import create_overlay
        data = {'id': 't1', 'type': 'ticker', 'config': {'text': 'hi'}, 'show_over_video': False}
        overlay = create_overlay(data)
        assert overlay is not None
        assert overlay.overlay_id == 't1'

    @patch('overlay_types.pygame')
    def test_creates_static_text(self, mock_pygame):
        mock_surf = MagicMock(get_width=MagicMock(return_value=100), get_height=MagicMock(return_value=30))
        mock_pygame.font.Font.return_value = MagicMock(render=MagicMock(return_value=mock_surf))
        mock_pygame.font.SysFont.return_value = mock_pygame.font.Font.return_value
        from overlay_types import create_overlay
        data = {'id': 's1', 'type': 'static_text', 'config': {'text': 'hi'}}
        overlay = create_overlay(data)
        assert overlay is not None

    def test_returns_none_for_unknown_type(self):
        from overlay_types import create_overlay
        data = {'id': 'x', 'type': 'nonexistent', 'config': {}}
        assert create_overlay(data) is None

    def test_returns_none_for_missing_type(self):
        from overlay_types import create_overlay
        data = {'id': 'x', 'config': {}}
        assert create_overlay(data) is None


class TestMakeBgColor:
    """Tests for _make_bg_color opacity blending."""

    def test_full_opacity(self):
        from overlay_types import _make_bg_color
        assert _make_bg_color('#FFFFFF', 1.0) == (255, 255, 255)

    def test_zero_opacity(self):
        from overlay_types import _make_bg_color
        assert _make_bg_color('#FFFFFF', 0.0) == (0, 0, 0)

    def test_half_opacity(self):
        from overlay_types import _make_bg_color
        r, g, b = _make_bg_color('#FF0000', 0.5)
        assert r == 127
        assert g == 0
        assert b == 0

    def test_partial_color(self):
        from overlay_types import _make_bg_color
        r, g, b = _make_bg_color('#1a1a2e', 0.85)
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255


class TestBaseOverlayUpdateConfig:
    """Tests for BaseOverlay.update_config() change detection."""

    def test_returns_true_on_change(self):
        from overlay_types import BaseOverlay
        overlay = BaseOverlay('o1', {'text': 'old'}, show_over_video=False)
        result = overlay.update_config({'text': 'new'}, False)
        assert result is True
        assert overlay.config == {'text': 'new'}

    def test_returns_false_when_unchanged(self):
        from overlay_types import BaseOverlay
        overlay = BaseOverlay('o1', {'text': 'same'}, show_over_video=False)
        result = overlay.update_config({'text': 'same'}, False)
        assert result is False

    def test_updates_show_over_video(self):
        from overlay_types import BaseOverlay
        overlay = BaseOverlay('o1', {}, show_over_video=False)
        overlay.update_config({}, True)
        assert overlay.show_over_video is True


class TestCountdownRemainingText:
    """Tests for CountdownOverlay._get_remaining_text() without pygame."""

    def _make_countdown(self, target_time=None, expired_text='TIME!'):
        from overlay_types import CountdownOverlay
        config = {'expired_text': expired_text}
        if target_time:
            config['target_time'] = target_time.isoformat()
        # Patch pygame for __init__
        with patch('overlay_types.pygame') as mock_pg:
            mock_surf = MagicMock(get_width=MagicMock(return_value=100), get_height=MagicMock(return_value=30))
            mock_pg.font.Font.return_value = MagicMock(render=MagicMock(return_value=mock_surf))
            mock_pg.font.SysFont.return_value = mock_pg.font.Font.return_value
            overlay = CountdownOverlay('cd1', config)
        return overlay

    def test_expired_returns_expired_text(self):
        past = datetime.now() - timedelta(hours=1)
        overlay = self._make_countdown(target_time=past, expired_text='LAST CALL!')
        assert overlay._get_remaining_text() == 'LAST CALL!'

    def test_no_target_returns_expired_text(self):
        overlay = self._make_countdown(target_time=None, expired_text='NO TIME')
        assert overlay._get_remaining_text() == 'NO TIME'

    def test_future_time_returns_formatted(self):
        future = datetime.now() + timedelta(hours=1, minutes=30, seconds=45)
        overlay = self._make_countdown(target_time=future)
        text = overlay._get_remaining_text()
        # Should be in H:MM:SS format
        assert ':' in text
        parts = text.split(':')
        assert len(parts) == 3  # hours:minutes:seconds

    def test_under_one_hour_no_hour_prefix(self):
        future = datetime.now() + timedelta(minutes=5, seconds=30)
        overlay = self._make_countdown(target_time=future)
        text = overlay._get_remaining_text()
        parts = text.split(':')
        assert len(parts) == 2  # MM:SS only

    def test_just_seconds_remaining(self):
        future = datetime.now() + timedelta(seconds=45)
        overlay = self._make_countdown(target_time=future)
        text = overlay._get_remaining_text()
        parts = text.split(':')
        assert len(parts) == 2  # 00:45


class TestOverlayTypeMap:
    """Tests for the OVERLAY_TYPE_MAP registry."""

    def test_all_five_types_registered(self):
        from overlay_types import OVERLAY_TYPE_MAP
        assert set(OVERLAY_TYPE_MAP.keys()) == {'ticker', 'static_text', 'image', 'countdown', 'qr_code'}

    def test_each_type_is_a_class(self):
        from overlay_types import OVERLAY_TYPE_MAP, BaseOverlay
        for cls in OVERLAY_TYPE_MAP.values():
            assert issubclass(cls, BaseOverlay)
