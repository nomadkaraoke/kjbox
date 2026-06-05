"""unified_search must find a '&' catalog row from an 'and' query (the original bug).

Route-level guard for Task 12: ``routes.unified_search`` now sends both the
query and the indexed text through the shared normalizer, so symbol
conjunctions ("&" / "+") meet "and" in one canonical space. This test drives
``unified_search`` directly with a lightweight app double (no Flask factory,
no network) and asserts the &-row surfaces for an "and" query.
"""
import types

import pytest

from catalog import ExternalCatalog
import routes


@pytest.fixture
def fake_app(tmp_path, monkeypatch):
    listing = tmp_path / "list.txt"
    listing.write_text("/m/KK-1 - Simon & Garfunkel - Sound Of Silence.zip\n")
    cat = ExternalCatalog({}, db_path=str(tmp_path / "external_media.db"))
    cat.init_schema()
    cat.build_from_file_list(str(listing))

    media = types.SimpleNamespace(index={})  # no downloaded-media rows
    app = types.SimpleNamespace(catalog=cat, media=media, kj_config={})

    # No network: KN + divebar return nothing.
    monkeypatch.setattr(routes.karaoke_nerds, "search", lambda *a, **k: [])
    if hasattr(routes, "divebar"):
        monkeypatch.setattr(routes.divebar, "search", lambda *a, **k: [])
    return app


def test_and_query_finds_ampersand_row(fake_app):
    result = routes.unified_search(
        "Sound Of Silence - Simon and Garfunkel", fake_app, grouped=False,
    )
    titles = [r.get("title") for r in result["local"]]
    assert "Sound Of Silence" in titles


def test_and_query_finds_ampersand_row_via_downloaded_media(tmp_path, monkeypatch):
    """Same bug, but the &-row lives in the downloaded-media index (not the
    external catalog) — exercises the local-media filter branch that Task 12
    routed through the shared normalizer."""
    cat = ExternalCatalog({}, db_path=str(tmp_path / "external_media.db"))
    cat.init_schema()  # empty catalog → no FTS hits

    media = types.SimpleNamespace(index={
        "/m/KK-1 - Simon & Garfunkel - Sound Of Silence.zip": {
            "filename": "KK-1 - Simon & Garfunkel - Sound Of Silence.zip",
            "display_name": "Simon & Garfunkel - Sound Of Silence",
        },
    })
    app = types.SimpleNamespace(catalog=cat, media=media, kj_config={})

    monkeypatch.setattr(routes.karaoke_nerds, "search", lambda *a, **k: [])
    if hasattr(routes, "divebar"):
        monkeypatch.setattr(routes.divebar, "search", lambda *a, **k: [])

    result = routes.unified_search(
        "Simon and Garfunkel Sound Of Silence", app, grouped=False,
    )
    titles = [r.get("title") for r in result["local"]]
    assert "Sound Of Silence" in titles
