"""Recs 5–6 of the search-unification handoff: server-composed endpoints.

- ``GET /library/search`` backs the Library panel's filter with the SAME
  engine as rotation search (``unified_search(local_only=True)``) so it gains
  typo tolerance the client-side JS term filter never had.
- ``POST /karaoke-nerds/search`` returns the full unified payload, including
  the new server-side ``local_path`` join (KN track's YouTube video already
  on disk), making the KN panel's client-side matching obsolete.
"""
import json
import types
from unittest.mock import patch

import routes

# One community row from the mocked Divebar CF, reused across the KN tests.
_KN_PAYLOAD = {
    "community": [
        {"artist": "Test Artist", "title": "Test Song", "brand": "CB",
         "watch": "https://www.youtube.com/watch?v=abc123defgh"},
    ],
    "full": [],
}


# --- /library/search ------------------------------------------------------

def test_library_search_short_query_rejected(flask_test_client):
    response = flask_test_client.get('/library/search?q=a')
    assert response.status_code == 400


def test_library_search_exact_match_from_media_index(flask_app, flask_test_client):
    flask_app.media.index["/downloads/Maximo Park - Books from Boxes.mp4"] = {
        "path": "/downloads/Maximo Park - Books from Boxes.mp4",
        "filename": "Maximo Park - Books from Boxes.mp4",
        "display_name": "Maximo Park - Books from Boxes",
    }
    response = flask_test_client.get('/library/search?q=books+from+boxes')
    assert response.status_code == 200
    rows = json.loads(response.data)["results"]
    assert any(r.get("title") == "Books from Boxes" for r in rows)


def test_library_search_typo_query_fuzzy_matches(flask_app, flask_test_client):
    """The Library filter now shares rotation search's typo tolerance:
    "boks" must find "Books from Boxes" (the JS term filter never could)."""
    flask_app.media.index["/downloads/Maximo Park - Books from Boxes.mp4"] = {
        "path": "/downloads/Maximo Park - Books from Boxes.mp4",
        "filename": "Maximo Park - Books from Boxes.mp4",
        "display_name": "Maximo Park - Books from Boxes",
    }
    response = flask_test_client.get(
        '/library/search?q=maximo+park+boks+from+boxes')
    assert response.status_code == 200
    rows = json.loads(response.data)["results"]
    assert any(r.get("title") == "Books from Boxes" for r in rows)


def test_library_search_never_calls_remote_backends(flask_app, flask_test_client):
    """local_only means NO Karaoke Nerds / Divebar calls (fast + offline)."""
    with patch.object(routes.karaoke_nerds, 'search') as mock_kn, \
            patch.object(routes.divebar, 'search') as mock_db:
        response = flask_test_client.get('/library/search?q=anything+at+all')
    assert response.status_code == 200
    mock_kn.assert_not_called()
    mock_db.assert_not_called()


def test_library_search_respects_limit(flask_app, flask_test_client):
    for i in range(6):
        path = f"/downloads/Artist - Crash Into Me {i}.mp4"
        flask_app.media.index[path] = {
            "path": path,
            "filename": f"Artist - Crash Into Me {i}.mp4",
            "display_name": f"Artist - Crash Into Me {i}",
        }
    response = flask_test_client.get('/library/search?q=crash+into+me&limit=3')
    assert response.status_code == 200
    assert len(json.loads(response.data)["results"]) == 3


# --- /karaoke-nerds/search local_path join --------------------------------

@patch('karaoke_nerds.divebar.kn_search')
def test_kn_search_attaches_local_path_for_downloaded_video(
        mock_kn, flask_app, flask_test_client):
    """A KN track whose YouTube video is already on disk gets local_path so
    the panel renders "Downloaded → Play" with no client-side id join."""
    mock_kn.return_value = _KN_PAYLOAD
    flask_app.media.index["/downloads/Test Artist - Test Song [yt-abc123defgh].mp4"] = {
        "path": "/downloads/Test Artist - Test Song [yt-abc123defgh].mp4",
        "filename": "Test Artist - Test Song [yt-abc123defgh].mp4",
        "display_name": "Test Artist - Test Song",
        "youtube_id": "abc123defgh",
    }

    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({"query": "test song"}),
        content_type='application/json')
    assert response.status_code == 200
    songs = json.loads(response.data)["karaoke_nerds"]
    assert len(songs) == 1
    track = songs[0]["tracks"][0]
    assert track["local_path"] == \
        "/downloads/Test Artist - Test Song [yt-abc123defgh].mp4"


@patch('karaoke_nerds.divebar.kn_search')
def test_kn_search_no_local_path_when_not_downloaded(
        mock_kn, flask_test_client):
    mock_kn.return_value = _KN_PAYLOAD
    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({"query": "test song"}),
        content_type='application/json')
    songs = json.loads(response.data)["karaoke_nerds"]
    assert "local_path" not in songs[0]["tracks"][0]


_MASTER_PATH = ("/downloads/NOMAD-720p/"
                "NOMAD-0729 - Maxïmo Park - Books from Boxes.mp4")
_MASTER_ENTRY = {
    "path": _MASTER_PATH,
    "filename": "NOMAD-0729 - Maxïmo Park - Books from Boxes.mp4",
    "display_name": "Maxïmo Park - Books from Boxes",
}
_KN_NOMAD_PAYLOAD = {
    "community": [
        # Accent/case differ from the on-disk master — the join must fold both.
        {"artist": "Maximo Park", "title": "Books From Boxes", "brand": "NOMAD",
         "watch": "https://www.youtube.com/watch?v=RlBlAKxyqZw"},
    ],
    "full": [],
}


@patch('karaoke_nerds.divebar.kn_search')
def test_kn_search_master_join_is_query_independent(
        mock_kn, flask_app, flask_test_client):
    """A KN NOMAD row gets local_path from the on-disk master even when the
    LOCAL search misses it (the master join scans the whole media index, like
    the client-side masterPathByNorm map it replaced — not just the rows the
    user's query happened to surface)."""
    mock_kn.return_value = _KN_NOMAD_PAYLOAD
    flask_app.media.index[_MASTER_PATH] = dict(_MASTER_ENTRY)

    # Query that matches nothing locally (the KN mock answers regardless).
    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({"query": "zz unrelated query"}),
        content_type='application/json')
    data = json.loads(response.data)
    assert data["local"] == []  # local search really did miss
    songs = data["karaoke_nerds"]
    assert len(songs) == 1
    assert songs[0]["tracks"][0]["local_path"] == _MASTER_PATH


@patch('karaoke_nerds.divebar.kn_search')
def test_kn_search_suppresses_mastered_nomad_row_when_local_hit(
        mock_kn, flask_app, flask_test_client):
    """When the local search DOES surface the master, the redundant KN NOMAD
    row is suppressed and the master rides in the payload's local rows."""
    mock_kn.return_value = _KN_NOMAD_PAYLOAD
    flask_app.media.index[_MASTER_PATH] = dict(_MASTER_ENTRY)

    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({"query": "books from boxes"}),
        content_type='application/json')
    data = json.loads(response.data)
    assert any(r.get("path") == _MASTER_PATH for r in data["local"])
    # The song's only track was the redundant NOMAD row -> song dropped.
    assert data["karaoke_nerds"] == []


# --- _attach_local_paths_to_kn unit ---------------------------------------

def test_attach_local_paths_media_id_token_fallback():
    """Entries without an explicit youtube_id fall back to the canonical
    ``yt-<vid>`` media_id token (post-v0.54.1 slug filenames)."""
    media = types.SimpleNamespace(index={
        "/d/song.mp4": {"media_id": "yt-abc123defgh"},
    })
    app = types.SimpleNamespace(media=media)
    kn = [{"artist": "A", "title": "T", "tracks": [
        {"brand_code": "CB",
         "youtube_url": "https://youtu.be/abc123defgh"},
        {"brand_code": "KV", "youtube_url": None},
    ]}]
    routes._attach_local_paths_to_kn(app, kn)
    assert kn[0]["tracks"][0]["local_path"] == "/d/song.mp4"
    assert "local_path" not in kn[0]["tracks"][1]
