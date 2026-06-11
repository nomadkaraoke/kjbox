"""Overlay configuration: schema, validation, defaults, and position calculation."""

import os

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
MARGIN = 20

OVERLAY_TYPES = {'ticker', 'static_text', 'image', 'countdown', 'qr_code', 'rotation_list'}

POSITION_PRESETS = {
    'top-left': 'top-left',
    'top-center': 'top-center',
    'top-right': 'top-right',
    'center': 'center',
    'bottom-left': 'bottom-left',
    'bottom-center': 'bottom-center',
    'bottom-right': 'bottom-right',
    'bottom': 'bottom',
    'top': 'top',
    'custom': 'custom',
}

# Default configs per overlay type
TYPE_DEFAULTS = {
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
    'static_text': {
        'text': '',
        'position': 'top-right',
        'custom_x': None,
        'custom_y': None,
        'font_size': 36,
        'font_family': 'sans',
        'bold': True,
        'italic': False,
        'max_width': 0,
        'text_align': 'left',
        'text_color': '#FFFFFF',
        'bg_color': '#000000',
        'bg_opacity': 0.7,
        'padding': 12,
    },
    'image': {
        'image_path': '',
        'position': 'top-right',
        'custom_x': None,
        'custom_y': None,
        'width': 150,
    },
    'countdown': {
        'target_time': '',
        'label': 'Time remaining',
        'expired_text': 'TIME!',
        'position': 'top-center',
        'custom_x': None,
        'custom_y': None,
        'font_size': 40,
        'text_color': '#FF4444',
        'bg_color': '#000000',
        'bg_opacity': 0.85,
        'padding': 15,
    },
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
    'rotation_list': {
        # The between-songs rotation home screen (heading + stats + singer list +
        # page dots), drawn over the desktop wallpaper. Reads the rotation cache
        # directly; no per-instance styling is required.
        'position': 'top-left',
    },
}


def hex_to_rgb(hex_color):
    """Convert hex color string to (r, g, b) tuple."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def apply_defaults(overlay):
    """Fill in missing config fields with type-specific defaults."""
    overlay_type = overlay.get('type', '')
    defaults = TYPE_DEFAULTS.get(overlay_type, {})
    config = overlay.get('config', {})
    for key, value in defaults.items():
        if key not in config:
            config[key] = value
    overlay['config'] = config
    # Top-level defaults
    overlay.setdefault('enabled', False)
    overlay.setdefault('show_over_video', False)
    overlay.setdefault('name', '')
    return overlay


def validate_overlay(overlay):
    """Validate an overlay config dict. Returns (is_valid, error_message)."""
    if not isinstance(overlay, dict):
        return False, 'Overlay must be a dict'

    overlay_type = overlay.get('type')
    if overlay_type not in OVERLAY_TYPES:
        return False, f'Invalid type: {overlay_type}. Must be one of {OVERLAY_TYPES}'

    if not overlay.get('id'):
        return False, 'Overlay must have an id'

    config = overlay.get('config', {})

    # Type-specific validation
    if overlay_type == 'ticker':
        source = config.get('source', 'static')
        if source == 'static' and not config.get('text'):
            return False, 'Static ticker overlay requires text'
    elif overlay_type == 'static_text':
        if not config.get('text'):
            return False, 'Static text overlay requires text'
    elif overlay_type == 'image':
        if not config.get('image_path'):
            return False, 'Image overlay requires image_path'
    elif overlay_type == 'countdown':
        if not config.get('target_time'):
            return False, 'Countdown overlay requires target_time'
    elif overlay_type == 'qr_code':
        if not config.get('url'):
            return False, 'QR code overlay requires url'

    return True, None


def calculate_position(position, width, height, custom_x=None, custom_y=None):
    """Calculate pixel (x, y) from a position preset and overlay dimensions.

    Returns (x, y) tuple.
    """
    if position == 'custom' and custom_x is not None and custom_y is not None:
        return (int(custom_x), int(custom_y))

    positions = {
        'top-left': (MARGIN, MARGIN),
        'top-center': ((SCREEN_WIDTH - width) // 2, MARGIN),
        'top-right': (SCREEN_WIDTH - width - MARGIN, MARGIN),
        'center': ((SCREEN_WIDTH - width) // 2, (SCREEN_HEIGHT - height) // 2),
        'bottom-left': (MARGIN, SCREEN_HEIGHT - height - MARGIN),
        'bottom-center': ((SCREEN_WIDTH - width) // 2, SCREEN_HEIGHT - height - MARGIN),
        'bottom-right': (SCREEN_WIDTH - width - MARGIN, SCREEN_HEIGHT - height - MARGIN),
        'bottom': (0, SCREEN_HEIGHT - height),
        'top': (0, 0),
    }
    return positions.get(position, (MARGIN, MARGIN))


def get_overlays_path():
    """Return the path to overlays.json, checking common locations."""
    # Check relative to this file's repo root
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(repo_root, 'data', 'overlays.json'),
        '/opt/nomad/kjbox/data/overlays.json',
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # Default to repo-relative path (will be created if needed)
    return candidates[0]
