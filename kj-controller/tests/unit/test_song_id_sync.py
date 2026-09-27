"""nomad-catalog-sync's song-identification step (scripts/sync_catalogs.run_song_id_sync)."""
import gzip
import json

import pytest

from scripts import build_song_id_db, sync_catalogs


def _shard(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")


@pytest.fixture
def gcs(tmp_path):
    """Fake bucket: manifest + two shards; records every download."""
    src = tmp_path / "bucket"
    src.mkdir()
    _shard(src / "a.tsv.gz", [("The Strokes", "Machu Picchu", 64, 1)])
    _shard(src / "b.tsv.gz", [("Rihanna", "Push Up On Me", 47, 0), ("Narrow Head", "See You Around", "", 1)])
    state = {"run": "20260927-043000", "calls": []}

    def download(uri, dest, key, gcloud_bin):
        state["calls"].append(uri)
        if uri == sync_catalogs.SONG_ID_MANIFEST_URI:
            with open(dest, "w") as f:
                json.dump({"run": state["run"], "shards": ["gs://b/song-id/r/a.tsv.gz",
                                                          "gs://b/song-id/r/b.tsv.gz"]}, f)
        else:
            with open(src / uri.rsplit("/", 1)[1], "rb") as s, open(dest, "wb") as d:
                d.write(s.read())
    state["download"] = download
    return state


def _cfg(tmp_path):
    return {"song_id_db": str(tmp_path / "song_id.db"), "master_sync_credentials_file": ""}


def test_builds_index_from_manifest_shards(tmp_path, gcs):
    r = sync_catalogs.run_song_id_sync(_cfg(tmp_path), requests_lib=None, download_gcs=gcs["download"],
                                       gcloud_bin="gcloud")
    assert r["error"] is None and r["changed"] is True and r["songs"] == 3
    meta = build_song_id_db.stored_meta(str(tmp_path / "song_id.db"))
    assert meta["source_run"] == "20260927-043000" and meta["songs"] == "3"


def test_same_run_is_skipped_new_run_rebuilds(tmp_path, gcs):
    cfg = _cfg(tmp_path)
    kw = dict(requests_lib=None, download_gcs=gcs["download"], gcloud_bin="gcloud")
    sync_catalogs.run_song_id_sync(cfg, **kw)
    gcs["calls"].clear()
    again = sync_catalogs.run_song_id_sync(cfg, **kw)
    assert again["skipped"] == "run unchanged"
    assert gcs["calls"] == [sync_catalogs.SONG_ID_MANIFEST_URI]      # no shard downloads
    gcs["run"] = "20260928-043000"
    assert sync_catalogs.run_song_id_sync(cfg, **kw)["changed"] is True


def test_manifest_failure_is_reported_not_raised(tmp_path):
    def boom(*a):
        raise RuntimeError("offline")
    r = sync_catalogs.run_song_id_sync(_cfg(tmp_path), requests_lib=None, download_gcs=boom,
                                       gcloud_bin="gcloud")
    assert r["changed"] is False and r["error"].startswith("manifest:")


def test_disabled(tmp_path):
    cfg = {**_cfg(tmp_path), "song_id_enabled": False}
    assert sync_catalogs.run_song_id_sync(cfg)["skipped"] == "disabled"


def test_reload_route_reopens_index(flask_test_client, flask_app, tmp_path, gcs):
    from song_identify import SongIdentifier
    sync_catalogs.run_song_id_sync(_cfg(tmp_path), requests_lib=None, download_gcs=gcs["download"],
                                   gcloud_bin="gcloud")
    flask_app.song_identifier = SongIdentifier(str(tmp_path / "song_id.db"))
    r = flask_test_client.post("/song-id/reload")
    assert r.status_code == 200 and r.get_json()["stats"]["songs"] == "3"
    assert flask_app.song_identifier.identify("the strokes max picu")["best"]["title"] == "Machu Picchu"
