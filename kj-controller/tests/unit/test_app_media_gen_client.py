"""Regression test: when gen is configured, the app factory must inject the
GenClient into the MediaIndex so downloads can LLM-refine their names."""

from app import create_app


def test_media_gets_gen_client_when_configured(mock_config, tmp_path):
    cfg = dict(mock_config)
    cfg["media_db_path"] = str(tmp_path / "media_library.db")
    cfg["gen_api_url"] = "https://api.example.com"
    cfg["gen_api_token"] = "tok"

    flask_app = create_app(config=cfg)
    try:
        assert flask_app.gen_client is not None
        assert flask_app.media.gen_client is flask_app.gen_client
    finally:
        flask_app.catalog.close()


def test_media_gen_client_none_when_gen_not_configured(mock_config, tmp_path):
    cfg = dict(mock_config)
    cfg["media_db_path"] = str(tmp_path / "media_library.db")
    cfg["gen_api_url"] = ""
    cfg["gen_api_token"] = ""

    flask_app = create_app(config=cfg)
    try:
        assert flask_app.gen_client is None
        assert flask_app.media.gen_client is None
    finally:
        flask_app.catalog.close()
