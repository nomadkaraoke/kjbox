from config import load_config


def test_media_and_sync_defaults_present(tmp_path):
    cfg = load_config(str(tmp_path / "nonexistent.json"))
    assert cfg["media_db_path"].endswith("media_library.db")
    assert cfg["master_sync_source"] == (
        "gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/"
    )
    assert cfg["master_sync_dest"] == ""          # "" -> derived under download_folder
    assert cfg["master_sync_credentials_file"] == ""
    assert cfg["master_sync_enabled"] is False    # opt-in; on-device config turns it on
