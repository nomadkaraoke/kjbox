"""Tests for the local catalog mirror (catalog_mirror.py + sync_catalogs.py).

The mirror replaces the two Divebar Cloud Function search calls with local
FTS5 searches, so the tests pin (a) result-shape parity with the remote
APIs, (b) the shared-engine matching semantics (accent fold, word order,
typo tolerance), and (c) the freshness gate + fallback behavior.
"""
import gzip
import json
import os
import sqlite3
import time

import pytest

import catalog_mirror
import divebar
import karaoke_nerds
from scripts import sync_catalogs


COMMUNITY_ITEMS = [
    {"Id": 1, "Artist": "Maxïmo Park", "Title": "Books From Boxes",
     "Brand": "NOMAD", "Watch": "https://youtu.be/abc"},
    {"Id": 2, "Artist": "Queen", "Title": "Bohemian Rhapsody",
     "Brand": "KV", "Watch": "https://youtu.be/def"},
]
FULL_ITEMS = [
    {"Id": 10, "Artist": "Jason Aldean", "Title": "Big Green Tractor",
     "Brands": "AC,ASK,CB,KV"},
    {"Id": 11, "Artist": "Maxïmo Park", "Title": "Books From Boxes",
     "Brands": "NOMAD"},
]
DIVEBAR_ROWS = [
    {"file_id": "f1", "brand": "Sandell", "brand_code": "SDK",
     "artist": "José Feliciano", "title": "Feliz Navidad",
     "filename": "x.cdg", "format": "cdg", "file_size": 1,
     "drive_path": "SDK/x.cdg", "in_gcs": True},
    {"file_id": "f2", "brand": "Sandell", "brand_code": "SDK",
     "artist": "José Feliciano", "title": "Feliz Navidad",
     "filename": "x.mp3", "format": "mp3", "file_size": 2,
     "drive_path": "SDK/x.mp3", "in_gcs": True},
    {"file_id": "f3", "brand": "Nomad Karaoke", "brand_code": "NOMAD",
     "artist": "Maxïmo Park", "title": "Books from Boxes",
     "filename": "y.mp4", "format": "mp4", "file_size": 3,
     "drive_path": "NOMAD/y.mp4", "in_gcs": False},
]


def _write_exports(tmp_path):
    community = tmp_path / "community.json.gz"
    full = tmp_path / "full.json.gz"
    dvb = tmp_path / "divebar.json.gz"
    with gzip.open(community, "wt", encoding="utf-8") as f:
        json.dump({"Items": COMMUNITY_ITEMS}, f)
    with gzip.open(full, "wt", encoding="utf-8") as f:
        json.dump({"Items": FULL_ITEMS}, f)
    with gzip.open(dvb, "wt", encoding="utf-8") as f:
        for row in DIVEBAR_ROWS:
            f.write(json.dumps(row) + "\n")
    return str(community), str(full), str(dvb)


@pytest.fixture
def mirror(tmp_path):
    community, full, dvb = _write_exports(tmp_path)
    db = str(tmp_path / "mirror.db")
    catalog_mirror.build_mirror_db(db, community, full, dvb,
                                   source_hashes={"a": "1"})
    m = catalog_mirror.CatalogMirror({"catalog_mirror_db": db})
    yield m
    m.close()


class TestBuild:
    def test_builds_all_three_sources(self, mirror):
        stats = mirror.stats()
        assert stats["sources"] == {
            "kn_community": 2, "kn_full": 2, "divebar": 3}
        assert stats["usable"] is True

    def test_build_is_atomic_replace(self, tmp_path, mirror):
        """A failed rebuild must leave the previous mirror intact."""
        with pytest.raises(FileNotFoundError):
            catalog_mirror.build_mirror_db(
                mirror.db_path, "/nonexistent.gz", "/nonexistent.gz",
                "/nonexistent.gz")
        mirror.reload()
        assert mirror.is_usable()
        assert mirror.stats()["sources"]["divebar"] == 3

    def test_stored_source_hashes_round_trip(self, mirror):
        assert catalog_mirror.stored_source_hashes(mirror.db_path) == {"a": "1"}
        assert catalog_mirror.stored_source_hashes("/nonexistent.db") is None


class TestSearchSemantics:
    def test_kn_search_shape_matches_remote(self, mirror):
        out = mirror.kn_search("big green tractor")
        assert out["community"] == []
        assert out["full"] == [{"artist": "Jason Aldean",
                                "title": "Big Green Tractor",
                                "brands": "AC,ASK,CB,KV"}]

    def test_community_rows_carry_watch_urls(self, mirror):
        out = mirror.kn_search("bohemian rhapsody queen")
        assert out["community"] == [{"artist": "Queen",
                                     "title": "Bohemian Rhapsody",
                                     "brand": "KV",
                                     "watch": "https://youtu.be/def"}]

    def test_accent_fold_both_directions(self, mirror):
        # ASCII query -> accented catalog rows, and accented query -> rows.
        assert len(mirror.divebar_search("jose feliciano feliz navidad")) == 2
        assert len(mirror.divebar_search("José Feliciano")) == 2

    def test_word_order_independent(self, mirror):
        assert len(mirror.divebar_search("books maximo park")) == 1
        assert mirror.kn_search("boxes from books maximo")["community"]

    def test_typo_tolerant_via_fuzzy_ladder(self, mirror):
        rows = mirror.divebar_search("maximo park boks")
        assert [r["file_id"] for r in rows] == ["f3"]

    def test_divebar_rows_group_like_remote(self, mirror):
        grouped = divebar.group_results(
            mirror.divebar_search("feliz navidad"))
        assert len(grouped) == 1
        assert {t["format"] for t in grouped[0]["tracks"]} == {"cdg", "mp3"}
        assert grouped[0]["tracks"][0]["brand_code"] == "SDK"

    def test_sub_trigram_query_still_substring_matches(self, mirror):
        # 1-2 char queries can't form a trigram, so the fuzzy ladder can't
        # serve them — a narrow LIKE fallback preserves substring recall
        # ("ee" is mid-word in "Queen").
        out = mirror.kn_search("ee")
        assert any(r["artist"] == "Queen" for r in out["community"])

    def test_unrelated_query_returns_nothing(self, mirror):
        assert mirror.divebar_search("taylor swift shake it off") == []
        assert mirror.kn_search("taylor swift shake it off") == {
            "community": [], "full": []}


class TestFreshnessGate:
    def test_missing_db_not_usable(self, tmp_path):
        m = catalog_mirror.CatalogMirror(
            {"catalog_mirror_db": str(tmp_path / "missing.db")})
        assert m.is_usable() is False

    def test_stale_mirror_not_usable(self, mirror):
        conn = sqlite3.connect(mirror.db_path)
        old = time.time() - 9 * 86400
        conn.execute("UPDATE mirror_meta SET value=? WHERE key='built_at'",
                     (str(old),))
        conn.commit()
        conn.close()
        mirror.reload()
        assert mirror.is_usable() is False
        # ...but a longer configured max age accepts it.
        mirror.config["catalog_mirror_max_age_days"] = 30
        assert mirror.is_usable() is True

    def test_normalizer_bump_invalidates(self, mirror):
        conn = sqlite3.connect(mirror.db_path)
        conn.execute(
            "UPDATE mirror_meta SET value='-1' WHERE key='normalizer_version'")
        conn.commit()
        conn.close()
        mirror.reload()
        assert mirror.is_usable() is False

    def test_kill_switch(self, mirror):
        mirror.config["catalog_mirror_enabled"] = False
        assert mirror.is_usable() is False


class TestKaraokeNerdsIntegration:
    def test_search_uses_fresh_mirror_not_cf(self, mirror, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("CF must not be called when mirror is fresh")
        monkeypatch.setattr(divebar, "kn_search", _boom)
        songs = karaoke_nerds.search("books maximo park", config={},
                                     mirror=mirror)
        assert songs and songs[0]["artist"] == "Maxïmo Park"
        # Community track first with URL, plus the merged full-catalog view.
        assert songs[0]["tracks"][0]["youtube_url"]

    def test_search_falls_back_when_mirror_stale(self, mirror, monkeypatch):
        mirror.config["catalog_mirror_enabled"] = False
        called = {}

        def fake_kn_search(q, config=None):
            called["q"] = q
            return {"community": [], "full": []}

        monkeypatch.setattr(divebar, "kn_search", fake_kn_search)
        karaoke_nerds.search("anything here", config={}, mirror=mirror)
        assert called["q"] == "anything here"

    def test_search_falls_back_when_mirror_raises(self, mirror, monkeypatch):
        monkeypatch.setattr(
            mirror, "kn_search",
            lambda q, limit=50: (_ for _ in ()).throw(RuntimeError("db gone")))
        monkeypatch.setattr(
            divebar, "kn_search",
            lambda q, config=None: {"community": [], "full": []})
        assert karaoke_nerds.search("x y z", config={}, mirror=mirror) == []


class TestSiblingPairingViaMirror:
    def test_find_sibling_audio_with_mirror_search(self, mirror):
        def search_fn(q):
            return divebar.group_results(mirror.divebar_search(q))
        sibling = divebar.find_sibling_audio(
            "f1", "Jose Feliciano", "Feliz Navidad", "SDK",
            search_fn=search_fn)
        assert sibling == {"file_id": "f2", "format": "mp3"}


class TestSyncScript:
    def _cfg(self, tmp_path):
        return {"catalog_mirror_db": str(tmp_path / "mirror.db"),
                "catalog_mirror_enabled": True,
                "master_sync_credentials_file": ""}

    def _fake_downloads(self, tmp_path):
        community, full, dvb = _write_exports(tmp_path)

        def fake_https(url, dest, requests_lib=None):
            with open(dvb, "rb") as src, open(dest, "wb") as out:
                out.write(src.read())

        def fake_gcs(uri, dest, key, gcloud_bin):
            src_path = community if "community" in uri else full
            with open(src_path, "rb") as src, open(dest, "wb") as out:
                out.write(src.read())

        return fake_https, fake_gcs

    def test_first_run_builds_and_pokes_reload(self, tmp_path):
        fake_https, fake_gcs = self._fake_downloads(tmp_path)
        poked = {}

        class FakeRequests:
            @staticmethod
            def post(url, timeout=None):
                poked["url"] = url

        result = sync_catalogs.run_sync(
            self._cfg(tmp_path), requests_lib=FakeRequests,
            download_https=fake_https, download_gcs=fake_gcs)
        assert result["error"] is None
        assert result["changed"] is True
        assert result["counts"]["divebar"] == 3
        assert poked["url"].endswith("/catalog-mirror/reload")

    def test_unchanged_sources_skip_rebuild(self, tmp_path):
        fake_https, fake_gcs = self._fake_downloads(tmp_path)
        cfg = self._cfg(tmp_path)
        first = sync_catalogs.run_sync(
            cfg, requests_lib=None,
            download_https=fake_https, download_gcs=fake_gcs)
        assert first["changed"] is True
        second = sync_catalogs.run_sync(
            cfg, requests_lib=None,
            download_https=fake_https, download_gcs=fake_gcs)
        assert second == {"changed": False, "skipped": "sources unchanged",
                          "error": None}

    def test_download_failure_is_reported_not_raised(self, tmp_path):
        def bad_https(url, dest, requests_lib=None):
            raise RuntimeError("offline")
        result = sync_catalogs.run_sync(
            self._cfg(tmp_path), requests_lib=None,
            download_https=bad_https, download_gcs=lambda *a: None)
        assert result["changed"] is False
        assert "offline" in result["error"]

    def test_disabled_config_skips(self, tmp_path):
        cfg = self._cfg(tmp_path)
        cfg["catalog_mirror_enabled"] = False
        assert sync_catalogs.run_sync(cfg)["skipped"] == "disabled"
