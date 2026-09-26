"""Singer search auto-correct: an empty search asks gen's free-text resolver
("the strokes max picu" → The Strokes — Machu Picchu) and re-searches."""
from unittest.mock import MagicMock

import pytest

import routes
import sing
from gen_client import GenApiError

SONG = {"key": "the strokes|machu picchu", "artist": "The Strokes", "title": "Machu Picchu",
        "version_count": 1, "versions": []}


@pytest.fixture(autouse=True)
def fresh():
    sing._resolve_cache.clear()
    sing._resolve_rate_state.clear()


@pytest.fixture
def gen(sing_app):
    g = MagicMock()
    g.singer_flow_configured.return_value = True
    g.resolve_search.return_value = {
        "kind": "content", "confident": True, "canonical_artist": "The Strokes",
        "canonical_title": "Machu Picchu", "typed_artist": "the strokes", "typed_title": "max picu"}
    sing_app.gen_client = g
    return g


@pytest.fixture
def searched(monkeypatch):
    calls = []

    def fake_unified(query, app, **kw):
        calls.append(query)
        return {"songs": [SONG] if query == "The Strokes Machu Picchu" else []}
    monkeypatch.setattr(routes, "unified_search", fake_unified)
    return calls


def _get(client, token, q="the strokes max picu"):
    return client.get("/sing/search/resolve", query_string={"q": q, "t": token, "device_id": "d" * 32})


def test_confident_correction_returns_corrected_results(client, token, gen, searched):
    body = _get(client, token).get_json()
    assert body["corrected"] == {"artist": "The Strokes", "title": "Machu Picchu"}
    assert body["typed"] == "the strokes max picu"
    assert body["songs"][0]["title"] == "Machu Picchu"
    assert searched == ["The Strokes Machu Picchu"]


def test_cached_per_query(client, token, gen, searched):
    _get(client, token)
    _get(client, token, "The Strokes  MAX picu")
    assert gen.resolve_search.call_count == 1


def test_correction_that_finds_nothing_is_not_offered(client, token, gen, searched):
    gen.resolve_search.return_value = {**gen.resolve_search.return_value,
                                       "canonical_title": "Unknown Song"}
    assert _get(client, token).get_json() == {}


@pytest.mark.parametrize("verdict", [
    {"kind": "content", "confident": False, "canonical_artist": "The Strokes", "canonical_title": "Machu Picchu"},
    {"kind": "none", "confident": False},
])
def test_unsure_verdict_changes_nothing(client, token, gen, searched, verdict):
    gen.resolve_search.return_value = verdict
    assert _get(client, token).get_json() == {}
    assert searched == []


def test_ambiguous_offers_did_you_mean(client, token, gen, searched):
    gen.resolve_search.return_value = {"kind": "ambiguous", "confident": False, "alternatives": [
        {"artist": "Radiohead", "title": "Creep"}, {"artist": "Stone Temple Pilots", "title": "Creep"},
        {"artist": "bad"}]}
    assert _get(client, token, "creep").get_json() == {"alternatives": [
        {"artist": "Radiohead", "title": "Creep"}, {"artist": "Stone Temple Pilots", "title": "Creep"}]}


def test_gen_down_or_unconfigured_is_silent(client, token, gen, searched, sing_app):
    gen.resolve_search.side_effect = GenApiError(0, "offline")
    assert _get(client, token).get_json() == {}
    gen.singer_flow_configured.return_value = False
    assert _get(client, token, "other query").get_json() == {}


def test_rate_limited_per_device(client, token, gen, searched, monkeypatch):
    monkeypatch.setattr(sing, "_RESOLVE_RATE_PER_DEVICE", 2)
    assert _get(client, token, "q one").status_code == 200
    assert _get(client, token, "q two").status_code == 200
    assert _get(client, token, "q three").status_code == 429


def test_needs_token(client, gen):
    assert client.get("/sing/search/resolve?q=abc").status_code in (401, 403)


def test_new_device_ids_cannot_bypass_the_venue_ceiling(client, token, gen, searched, monkeypatch):
    monkeypatch.setattr(sing, "_RESOLVE_RATE_PER_IP", 3)
    for i in range(3):
        r = client.get("/sing/search/resolve", query_string={"q": f"q {i}", "t": token, "device_id": f"{i:032d}"})
        assert r.status_code == 200
    r = client.get("/sing/search/resolve", query_string={"q": "q x", "t": token, "device_id": "f" * 32})
    assert r.status_code == 429
