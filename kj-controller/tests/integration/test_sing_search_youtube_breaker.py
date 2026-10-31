"""While the YouTube breaker is open, singer search stops offering YouTube-only
versions (and songs that only have those); the KJ's own search is untouched."""
import pytest

import routes

LOCAL = {"source": "local", "local": {"path": "/m/a.mp4"}}
YT = {"source": "kn", "kn": {"youtube_url": "https://youtu.be/y"}}
SONGS = [
    {"key": "a", "artist": "A", "title": "Mixed", "version_count": 2, "versions": [YT, LOCAL]},
    {"key": "b", "artist": "B", "title": "YouTube only", "version_count": 1, "versions": [YT]},
]


@pytest.fixture(autouse=True)
def searched(monkeypatch):
    monkeypatch.setattr(routes, "unified_search",
                        lambda query, app, **kw: {"songs": [dict(s) for s in SONGS]})


def _search(client, token):
    return client.get("/sing/search", query_string={"q": "some song", "t": token}).get_json()


def test_breaker_closed_offers_everything(client, token):
    songs = _search(client, token)["songs"]
    assert [s["key"] for s in songs] == ["a", "b"]
    assert songs[0]["version_count"] == 2


def test_breaker_open_hides_youtube_only_versions(client, token, sing_app):
    sing_app.youtube_breaker.trip("yt-dlp broken", "https://youtu.be/x")
    songs = _search(client, token)["songs"]
    assert [s["key"] for s in songs] == ["a"]
    assert songs[0]["versions"] == [LOCAL]
    assert songs[0]["version_count"] == 1
