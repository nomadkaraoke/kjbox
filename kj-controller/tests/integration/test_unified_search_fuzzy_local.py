"""unified_search must surface an already-downloaded file for a TYPO query.

Before this change, the downloaded-media (media.index) branch of
``routes.unified_search`` only did exact normalized-substring matching, so a
misspelled query like "books from boxs" found the Karaoke Nerds / Divebar
community release (which matches server-side, typo-tolerant) but NOT the local
file the KJ already has — leaving no "Link" affordance. The media-index branch
now has a second, precision-gated fuzzy pass mirroring the catalog fallback.
"""
import types

import pytest

from catalog import ExternalCatalog
import routes


def _fake_app(tmp_path, index):
    cat = ExternalCatalog({}, db_path=str(tmp_path / "external_media.db"))
    cat.init_schema()  # empty external catalog → only media.index can match
    media = types.SimpleNamespace(index=index)
    return types.SimpleNamespace(catalog=cat, media=media, kj_config={})


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(routes.karaoke_nerds, "search", lambda *a, **k: [])
    if hasattr(routes, "divebar"):
        monkeypatch.setattr(routes.divebar, "search", lambda *a, **k: [])


def test_typo_query_surfaces_downloaded_file(tmp_path):
    app = _fake_app(tmp_path, {
        "/downloads/NOMAD-720p/NOMAD-0729 - Maximo Park - Books from Boxes.mp4": {
            "filename": "NOMAD-0729 - Maximo Park - Books from Boxes.mp4",
            "display_name": "Maximo Park - Books from Boxes",
        },
    })

    # Typo: "boxs" instead of "boxes".
    result = routes.unified_search("Maximo Park Books from boxs", app, grouped=False)
    titles = [r.get("title") for r in result["local"]]
    assert "Books from Boxes" in titles


def test_exact_query_still_matches(tmp_path):
    """Regression guard: the exact-substring path is unchanged by the fuzzy add."""
    app = _fake_app(tmp_path, {
        "/downloads/NOMAD-720p/NOMAD-0729 - Maximo Park - Books from Boxes.mp4": {
            "filename": "NOMAD-0729 - Maximo Park - Books from Boxes.mp4",
            "display_name": "Maximo Park - Books from Boxes",
        },
    })
    result = routes.unified_search("Maximo Park Books from Boxes", app, grouped=False)
    titles = [r.get("title") for r in result["local"]]
    assert "Books from Boxes" in titles


def test_unrelated_query_does_not_fuzzy_match(tmp_path):
    """Precision guard: a totally different query must not drag the file in."""
    app = _fake_app(tmp_path, {
        "/downloads/NOMAD-720p/NOMAD-0729 - Maximo Park - Books from Boxes.mp4": {
            "filename": "NOMAD-0729 - Maximo Park - Books from Boxes.mp4",
            "display_name": "Maximo Park - Books from Boxes",
        },
    })
    result = routes.unified_search("Bohemian Rhapsody Queen", app, grouped=False)
    assert result["local"] == []


def test_fuzzy_matches_are_capped(tmp_path, monkeypatch):
    """A loose typo query can't flood the picker beyond LOCAL_FUZZY_LIMIT."""
    monkeypatch.setattr(routes, "LOCAL_FUZZY_LIMIT", 3)
    index = {
        f"/downloads/dave-song-{i}.mp4": {
            "filename": f"KK-{i} - Dave Matthews Band - Crash Into Me {i}.mp4",
            "display_name": f"Dave Matthews Band - Crash Into Me {i}",
        }
        for i in range(10)
    }
    app = _fake_app(tmp_path, index)
    # Typo "matthws" so every row is a fuzzy (not exact) hit.
    result = routes.unified_search("Dave Matthws Band Crash Into Me", app, grouped=False)
    assert len(result["local"]) == 3


def test_local_only_path_also_gets_fuzzy(tmp_path):
    """The live 'Try Another' fast path (local_only) inherits typo tolerance."""
    app = _fake_app(tmp_path, {
        "/downloads/NOMAD-720p/NOMAD-0729 - Maximo Park - Books from Boxes.mp4": {
            "filename": "NOMAD-0729 - Maximo Park - Books from Boxes.mp4",
            "display_name": "Maximo Park - Books from Boxes",
        },
    })
    result = routes.unified_search(
        "Maximo Park Books from boxs", app, grouped=False, local_only=True)
    titles = [r.get("title") for r in result["local"]]
    assert "Books from Boxes" in titles
