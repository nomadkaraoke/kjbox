"""Tests for configuration: is_pi, load_config."""

import json

from config import is_pi, load_config


def test_is_pi_returns_false_without_dietpi(mocker):
    mocker.patch('config.os.path.exists', return_value=False)
    assert is_pi() is False


def test_is_pi_returns_true_with_dietpi(mocker):
    mocker.patch('config.os.path.exists', return_value=True)
    assert is_pi() is True


def test_is_pi_checks_correct_path(mocker):
    mock_exists = mocker.patch('config.os.path.exists', return_value=False)
    is_pi()
    mock_exists.assert_called_once_with('/boot/dietpi.txt')


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(config_file=str(tmp_path / 'nonexistent.json'))
    assert 'download_folder' in config
    assert 'media_folders' in config
    assert 'flask_port' in config
    assert config['flask_port'] == 5000


def test_load_config_merges_user_config(tmp_path):
    config_file = tmp_path / 'config.json'
    config_file.write_text(json.dumps({"flask_port": 9999, "custom_key": "value"}))
    config = load_config(config_file=str(config_file))
    assert config['flask_port'] == 9999
    assert config['custom_key'] == 'value'
    # Defaults still present
    assert 'download_folder' in config


def test_load_config_handles_corrupt_json(tmp_path):
    config_file = tmp_path / 'config.json'
    config_file.write_text('not valid json{{{')
    config = load_config(config_file=str(config_file))
    # Falls back to defaults
    assert config['flask_port'] == 5000


def test_save_config_value(tmp_path):
    """save_config_value persists a key to config.json."""
    import config as config_mod
    from config import save_config_value
    original = config_mod.CONFIG_FILE
    config_mod.CONFIG_FILE = str(tmp_path / 'config.json')
    try:
        save_config_value('test_key', 'test_value')
        with open(config_mod.CONFIG_FILE) as f:
            data = json.load(f)
        assert data['test_key'] == 'test_value'
    finally:
        config_mod.CONFIG_FILE = original


def test_save_config_value_merges_existing(tmp_path):
    """save_config_value merges into existing config file."""
    import config as config_mod
    from config import save_config_value
    original = config_mod.CONFIG_FILE
    config_file = tmp_path / 'config.json'
    config_file.write_text(json.dumps({"existing": "value"}))
    config_mod.CONFIG_FILE = str(config_file)
    try:
        save_config_value('new_key', 42)
        with open(config_mod.CONFIG_FILE) as f:
            data = json.load(f)
        assert data['existing'] == 'value'
        assert data['new_key'] == 42
    finally:
        config_mod.CONFIG_FILE = original
