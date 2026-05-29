"""Unit tests for desktop/overlay_config.py."""

import os
import sys

# Add desktop/ directory to path for imports
desktop_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'desktop')
sys.path.insert(0, os.path.abspath(desktop_dir))

from overlay_config import (
    OVERLAY_TYPES,
    apply_defaults,
    calculate_position,
    hex_to_rgb,
    validate_overlay,
)


class TestHexToRgb:
    def test_basic_white(self):
        assert hex_to_rgb('#FFFFFF') == (255, 255, 255)

    def test_basic_black(self):
        assert hex_to_rgb('#000000') == (0, 0, 0)

    def test_no_hash(self):
        assert hex_to_rgb('FF0000') == (255, 0, 0)

    def test_short_form(self):
        assert hex_to_rgb('#FFF') == (255, 255, 255)

    def test_mixed_case(self):
        assert hex_to_rgb('#ff7acc') == (255, 122, 204)


class TestCalculatePosition:
    def test_top_left(self):
        x, y = calculate_position('top-left', 100, 50)
        assert x == 20
        assert y == 20

    def test_top_right(self):
        x, y = calculate_position('top-right', 100, 50)
        assert x == 1920 - 100 - 20
        assert y == 20

    def test_center(self):
        x, y = calculate_position('center', 200, 100)
        assert x == (1920 - 200) // 2
        assert y == (1080 - 100) // 2

    def test_bottom_full_width(self):
        x, y = calculate_position('bottom', 1920, 50)
        assert x == 0
        assert y == 1080 - 50

    def test_top_full_width(self):
        x, y = calculate_position('top', 1920, 50)
        assert x == 0
        assert y == 0

    def test_custom_position(self):
        x, y = calculate_position('custom', 100, 50, custom_x=500, custom_y=300)
        assert x == 500
        assert y == 300

    def test_custom_without_coords_falls_back(self):
        x, y = calculate_position('custom', 100, 50)
        assert x == 20  # Falls back to top-left
        assert y == 20

    def test_unknown_position_defaults(self):
        x, y = calculate_position('unknown', 100, 50)
        assert x == 20
        assert y == 20


class TestApplyDefaults:
    def test_ticker_defaults(self):
        overlay = {'type': 'ticker', 'config': {'text': 'Hello'}}
        result = apply_defaults(overlay)
        assert result['config']['speed'] == 2
        assert result['config']['font_size'] == 28
        assert result['config']['text'] == 'Hello'  # Existing values preserved
        assert result['enabled'] is False  # Top-level default

    def test_static_text_defaults(self):
        overlay = {'type': 'static_text', 'config': {}}
        result = apply_defaults(overlay)
        assert result['config']['font_size'] == 36
        assert result['config']['position'] == 'top-right'

    def test_qr_code_defaults(self):
        overlay = {'type': 'qr_code', 'config': {'url': 'https://test.com'}}
        result = apply_defaults(overlay)
        assert result['config']['size'] == 180
        assert result['config']['position'] == 'bottom-right'

    def test_preserves_existing_values(self):
        overlay = {'type': 'ticker', 'config': {'speed': 5, 'font_size': 50}}
        result = apply_defaults(overlay)
        assert result['config']['speed'] == 5
        assert result['config']['font_size'] == 50


class TestValidateOverlay:
    def test_valid_ticker(self):
        valid, err = validate_overlay({
            'id': 'test1',
            'type': 'ticker',
            'config': {'text': 'Hello'},
        })
        assert valid is True
        assert err is None

    def test_invalid_type(self):
        valid, err = validate_overlay({
            'id': 'test1',
            'type': 'invalid',
            'config': {},
        })
        assert valid is False
        assert 'Invalid type' in err

    def test_missing_id(self):
        valid, err = validate_overlay({
            'type': 'ticker',
            'config': {'text': 'test'},
        })
        assert valid is False
        assert 'id' in err

    def test_not_a_dict(self):
        valid, err = validate_overlay('not a dict')
        assert valid is False

    def test_ticker_requires_text(self):
        valid, err = validate_overlay({
            'id': 'test1',
            'type': 'ticker',
            'config': {},
        })
        assert valid is False
        assert 'text' in err

    def test_image_requires_path(self):
        valid, err = validate_overlay({
            'id': 'test1',
            'type': 'image',
            'config': {},
        })
        assert valid is False
        assert 'image_path' in err

    def test_countdown_requires_target_time(self):
        valid, err = validate_overlay({
            'id': 'test1',
            'type': 'countdown',
            'config': {},
        })
        assert valid is False
        assert 'target_time' in err

    def test_qr_code_requires_url(self):
        valid, err = validate_overlay({
            'id': 'test1',
            'type': 'qr_code',
            'config': {},
        })
        assert valid is False
        assert 'url' in err

    def test_all_types_recognized(self):
        assert OVERLAY_TYPES == {'ticker', 'static_text', 'image', 'countdown', 'qr_code'}


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
