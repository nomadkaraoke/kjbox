"""OverlayManager: CRUD operations and state management for display overlays."""

import copy
import json
import os
import tempfile
import uuid


# Default path relative to kj-controller app dir
_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'overlays.json',
)

OVERLAY_TYPES = {'ticker', 'static_text', 'image', 'countdown', 'qr_code', 'rotation_list'}

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
    'rotation-list': {
        'type': 'rotation_list',
        'name': 'Rotation list',
        'enabled': True,
        'show_over_video': False,  # between-songs home screen; hidden during video
        'config': {
            'position': 'top-left',
        },
    },
}


class OverlayManager:
    """Manages overlay configurations persisted to overlays.json."""

    def __init__(self, config_path=None):
        self.config_path = config_path or _DEFAULT_PATH
        self._data = {'karaoke_playing': False, 'overlays': []}
        self._load()

    def _load(self):
        """Load overlays.json from disk."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {'karaoke_playing': False, 'overlays': []}
        else:
            self._data = {'karaoke_playing': False, 'overlays': []}

    def _save(self):
        """Atomically write overlays.json to disk."""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        dir_name = os.path.dirname(self.config_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.json')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self._data, f, indent=2)
                f.write('\n')
            os.replace(tmp_path, self.config_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_overlays(self):
        """Return list of all overlay configs."""
        return self._data.get('overlays', [])

    def get_overlay(self, overlay_id):
        """Get a single overlay by ID. Returns None if not found."""
        for overlay in self._data.get('overlays', []):
            if overlay.get('id') == overlay_id:
                return overlay
        return None

    def create_overlay(self, overlay_data):
        """Create a new overlay. Returns the created overlay with generated ID."""
        overlay_type = overlay_data.get('type')
        if overlay_type not in OVERLAY_TYPES:
            raise ValueError(f'Invalid overlay type: {overlay_type}')

        overlay = {
            'id': str(uuid.uuid4())[:8],
            'type': overlay_type,
            'name': overlay_data.get('name', ''),
            'enabled': overlay_data.get('enabled', False),
            'show_over_video': overlay_data.get('show_over_video', False),
            'config': overlay_data.get('config', {}),
        }

        self._data.setdefault('overlays', []).append(overlay)
        self._save()
        return overlay

    def update_overlay(self, overlay_id, updates):
        """Update an existing overlay. Returns updated overlay or None."""
        for i, overlay in enumerate(self._data.get('overlays', [])):
            if overlay.get('id') == overlay_id:
                # Update allowed fields
                for key in ('name', 'enabled', 'show_over_video', 'config'):
                    if key in updates:
                        overlay[key] = updates[key]
                self._data['overlays'][i] = overlay
                self._save()
                return overlay
        return None

    def delete_overlay(self, overlay_id):
        """Delete an overlay by ID. Returns True if deleted."""
        overlays = self._data.get('overlays', [])
        original_len = len(overlays)
        self._data['overlays'] = [o for o in overlays if o.get('id') != overlay_id]
        if len(self._data['overlays']) < original_len:
            self._save()
            return True
        return False

    def toggle_enabled(self, overlay_id):
        """Toggle the enabled state of an overlay. Returns updated overlay or None."""
        overlay = self.get_overlay(overlay_id)
        if overlay:
            return self.update_overlay(overlay_id, {'enabled': not overlay.get('enabled', False)})
        return None

    def toggle_show_over_video(self, overlay_id):
        """Toggle the show_over_video state. Returns updated overlay or None."""
        overlay = self.get_overlay(overlay_id)
        if overlay:
            return self.update_overlay(overlay_id, {
                'show_over_video': not overlay.get('show_over_video', False),
            })
        return None

    def import_overlays(self, overlays):
        """Replace all overlays with imported list. Assigns new IDs."""
        imported = []
        for o in overlays:
            if o.get('type') not in OVERLAY_TYPES:
                continue
            overlay = {
                'id': str(uuid.uuid4())[:8],
                'type': o['type'],
                'name': o.get('name', ''),
                'enabled': o.get('enabled', False),
                'show_over_video': o.get('show_over_video', False),
                'config': o.get('config', {}),
            }
            imported.append(overlay)
        self._data['overlays'] = imported
        self._save()
        return imported

    def create_preset(self, preset_name):
        """Create a new overlay from a named preset. Returns the created overlay.

        Raises ValueError if the preset name is unknown.
        """
        if preset_name not in OVERLAY_PRESETS:
            raise ValueError(f'Unknown preset: {preset_name}')
        template = copy.deepcopy(OVERLAY_PRESETS[preset_name])
        return self.create_overlay(template)

    def set_karaoke_playing(self, playing):
        """Update the karaoke_playing state flag."""
        if self._data.get('karaoke_playing') != playing:
            self._data['karaoke_playing'] = playing
            self._save()

    @property
    def karaoke_playing(self):
        return self._data.get('karaoke_playing', False)
