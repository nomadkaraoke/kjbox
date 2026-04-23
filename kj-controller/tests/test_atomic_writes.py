"""Tests for atomic write behavior in config.py and media.py.

Verifies that power-loss-safe write patterns (temp + fsync + rename) are used,
so a crash mid-write cannot corrupt the original file.
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

# Ensure kj-controller directory is on sys.path
app_dir = os.path.join(os.path.dirname(__file__), '..')
if app_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(app_dir))


class TestConfigAtomicWrite:
    """Tests for save_config_value atomic write behavior."""

    def test_save_creates_file(self, tmp_path):
        """save_config_value creates config.json if it doesn't exist."""
        config_file = tmp_path / "config.json"
        with patch('config.CONFIG_FILE', str(config_file)):
            from config import save_config_value
            save_config_value('test_key', 'test_value')

        data = json.loads(config_file.read_text())
        assert data['test_key'] == 'test_value'

    def test_save_preserves_existing_keys(self, tmp_path):
        """save_config_value preserves existing keys when adding new ones."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"existing": "value"}))
        with patch('config.CONFIG_FILE', str(config_file)):
            from config import save_config_value
            save_config_value('new_key', 42)

        data = json.loads(config_file.read_text())
        assert data['existing'] == 'value'
        assert data['new_key'] == 42

    def test_save_overwrites_existing_key(self, tmp_path):
        """save_config_value updates an existing key's value."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"key": "old"}))
        with patch('config.CONFIG_FILE', str(config_file)):
            from config import save_config_value
            save_config_value('key', 'new')

        data = json.loads(config_file.read_text())
        assert data['key'] == 'new'

    def test_save_no_temp_files_left(self, tmp_path):
        """No temporary files remain after successful save."""
        config_file = tmp_path / "config.json"
        with patch('config.CONFIG_FILE', str(config_file)):
            from config import save_config_value
            save_config_value('key', 'value')

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == 'config.json'

    def test_save_original_survives_write_error(self, tmp_path):
        """If the write fails, the original file is not corrupted."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"original": True}))

        with patch('config.CONFIG_FILE', str(config_file)):
            with patch('json.dump', side_effect=IOError("disk full")):
                from config import save_config_value
                with pytest.raises(Exception):
                    save_config_value('bad', 'data')

        # Original file should be intact
        data = json.loads(config_file.read_text())
        assert data == {"original": True}

    def test_save_cleans_up_temp_on_error(self, tmp_path):
        """Temporary file is cleaned up if write fails."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"original": True}))

        with patch('config.CONFIG_FILE', str(config_file)):
            with patch('json.dump', side_effect=IOError("disk full")):
                from config import save_config_value
                with pytest.raises(Exception):
                    save_config_value('bad', 'data')

        # Only the original config.json should remain
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == 'config.json'


class TestMediaIndexAtomicWrite:
    """Tests for MediaIndex.save atomic write behavior."""

    def test_save_creates_index(self, mock_config):
        """MediaIndex.save creates media_index.json."""
        from media import MediaIndex
        mi = MediaIndex(mock_config)
        mi.index = {"path/to/file.mp4": {"filename": "file.mp4"}}
        mi.save()

        with open(mock_config['media_index_path'], 'r') as f:
            data = json.load(f)
        assert "path/to/file.mp4" in data

    def test_save_no_temp_files_left(self, mock_config):
        """No temporary files remain after successful save."""
        from media import MediaIndex
        mi = MediaIndex(mock_config)
        mi.index = {"test": "data"}
        mi.save()

        index_dir = os.path.dirname(mock_config['media_index_path'])
        files = os.listdir(index_dir)
        json_files = [f for f in files if f.endswith('.json')]
        assert json_files == ['media_index.json']

    def test_save_original_survives_write_error(self, mock_config):
        """If the write fails, the original index is not corrupted."""
        from media import MediaIndex
        mi = MediaIndex(mock_config)
        mi.index = {"original": {"filename": "orig.mp4"}}
        mi.save()

        # Now simulate a failed write
        mi.index = {"corrupted": {"filename": "bad.mp4"}}
        with patch('json.dump', side_effect=IOError("disk full")):
            mi.save()

        # Original data should be intact
        with open(mock_config['media_index_path'], 'r') as f:
            data = json.load(f)
        assert "original" in data
        assert "corrupted" not in data

    def test_save_cleans_up_temp_on_error(self, mock_config):
        """Temporary file is cleaned up if write fails."""
        from media import MediaIndex
        mi = MediaIndex(mock_config)
        mi.index = {"original": {"filename": "orig.mp4"}}
        mi.save()

        with patch('json.dump', side_effect=IOError("disk full")):
            mi.save()

        index_dir = os.path.dirname(mock_config['media_index_path'])
        files = os.listdir(index_dir)
        json_files = [f for f in files if f.endswith('.json')]
        assert json_files == ['media_index.json']
