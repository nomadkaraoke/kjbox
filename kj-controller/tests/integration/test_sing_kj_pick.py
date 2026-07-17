"""Tests for the ``kj_pick`` source_type (Phase A).

A ``kj_pick`` request captures the singer's candidate-version snapshot and
defers the actual version selection to the KJ at approval time. These tests
lock down:

  * The ``_validate_kj_pick_payload`` helper rejects malformed snapshots.
  * ``/sing/submit`` accepts well-formed kj_pick bodies and persists the
    versions into ``source_meta``.
  * ``/sing/submit`` rejects kj_pick bodies that are missing the required
    song_artist / song_title (we still need a display label in the admin UI).
  * Auto-approve binds a kj_pick to its highest-priority version (the same one
    the admin picker marks ⭐ BEST) so the rotation entry gets a real file
    instead of being deferred to the KJ.
"""

import json

import pytest

from sing import _validate_kj_pick_payload
from routes import _pick_version_from_kj_pick


def _kj_pick_body(**overrides):
    body = {
        "singer_name": "Andrew",
        "phone": "+61 400 123 456",
        "song_artist": "Queen",
        "song_title": "Bohemian Rhapsody",
        "source_type": "kj_pick",
        "source_ref": None,
        "source_meta": {
            "versions": [
                {"kind": "local", "path": "/media/queen-bo-rhap.mp4"},
                {"kind": "kn", "brand_code": "SF", "youtube_url": "https://yt/1"},
            ],
        },
    }
    body.update(overrides)
    return body


class TestValidateHelper:
    def test_rejects_missing_source_meta(self):
        assert _validate_kj_pick_payload({}) is not None

    def test_rejects_empty_versions(self):
        err = _validate_kj_pick_payload({"source_meta": {"versions": []}})
        assert err is not None
        assert "versions" in err

    def test_rejects_versions_not_a_list(self):
        err = _validate_kj_pick_payload({"source_meta": {"versions": "nope"}})
        assert err is not None

    def test_rejects_missing_versions_key(self):
        err = _validate_kj_pick_payload({"source_meta": {}})
        assert err is not None

    def test_accepts_single_version(self):
        err = _validate_kj_pick_payload({
            "source_meta": {"versions": [{"kind": "local", "path": "/x.mp4"}]}
        })
        assert err is None

    def test_accepts_many_versions_up_to_cap(self):
        versions = [{"kind": "local", "path": f"/v{i}.mp4"} for i in range(50)]
        err = _validate_kj_pick_payload({"source_meta": {"versions": versions}})
        assert err is None

    def test_rejects_pathological_version_count(self):
        versions = [{"kind": "local", "path": f"/v{i}.mp4"} for i in range(51)]
        err = _validate_kj_pick_payload({"source_meta": {"versions": versions}})
        assert err is not None
        assert "too many" in err.lower()


class TestSubmitKjPick:
    def test_happy_path_creates_pending_request(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json=_kj_pick_body())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is False
        req = data["request"]
        assert req["source_type"] == "kj_pick"
        assert req["status"] == "pending"
        # Public view hides source_meta (it's admin-only) — fetch from the store
        # to confirm the versions snapshot round-tripped through JSON storage.
        stored = sing_app.sing_store.get_request(req["id"])
        versions = json.loads(stored["source_meta"])["versions"]
        assert versions[0]["kind"] == "local"
        assert versions[1]["brand_code"] == "SF"

    def test_rejects_missing_artist(self, client, token):
        body = _kj_pick_body(song_artist="")
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400

    def test_rejects_missing_title(self, client, token):
        body = _kj_pick_body(song_title="")
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400

    def test_rejects_missing_versions(self, client, token):
        body = _kj_pick_body(source_meta={})
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "versions" in resp.get_json()["error"]

    def test_rejects_empty_versions_array(self, client, token):
        body = _kj_pick_body(source_meta={"versions": []})
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400


class TestKjPickAutoApprove:
    """With auto-approve on, a kj_pick binds to its best version automatically."""

    def _snapshot(self):
        # A real candidate snapshot (source/local/kn shape). The local version
        # sits at index 1 on purpose: auto-pick must RANK (local beats a bare
        # YouTube link) rather than blindly grabbing index 0.
        return {
            "versions": [
                {"source": "kn", "kn": {"youtube_url": "https://yt/only"}},
                {"source": "local", "local": {
                    "path": "/media/queen-bo-rhap.mp4",
                    "artist": "Queen", "title": "Bohemian Rhapsody"}},
            ]
        }

    def test_auto_approve_binds_best_version_and_queues_entry(
        self, client, sing_app, token
    ):
        sing_app.sing_store.set_auto_approve(True)
        body = _kj_pick_body(source_meta=self._snapshot())
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is True
        assert data["request"]["status"] == "approved"
        # The local version (index 1) won the ranking, so the row was rewritten
        # to a concrete local source pointing at that file — no download, no
        # deferral to the KJ.
        stored = sing_app.sing_store.get_request(data["request"]["id"])
        assert stored["source_type"] == "local"
        assert stored["source_ref"] == "/media/queen-bo-rhap.mp4"
        entries = sing_app.rotation.get_rotation()
        assert any(e["singer"] == "Andrew" for e in entries)

    def test_auto_approve_falls_through_malformed_best_version(
        self, client, sing_app, token
    ):
        # A guest KJ must never be stuck approving: when the best-ranked version
        # can't be resolved (here a local entry with no path), auto-pick walks
        # to the next playable option rather than leaving the request pending.
        sing_app.sing_store.set_auto_approve(True)
        snapshot = {"versions": [
            {"source": "local", "local": {}},                       # best rank, but no path
            {"source": "local", "local": {"path": "/media/fallback.mp4"}},
        ]}
        resp = client.post(
            f"/sing/submit?t={token}", json=_kj_pick_body(source_meta=snapshot))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is True
        stored = sing_app.sing_store.get_request(data["request"]["id"])
        assert stored["source_type"] == "local"
        assert stored["source_ref"] == "/media/fallback.mp4"

    def test_pending_when_auto_approve_off(self, client, sing_app, token):
        # Default (auto-approve off) still defers to the KJ's review queue.
        body = _kj_pick_body(source_meta=self._snapshot())
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is False
        assert data["request"]["status"] == "pending"
        assert sing_app.sing_store.get_request(
            data["request"]["id"])["source_type"] == "kj_pick"

    def test_rejects_too_many_versions(self, client, token):
        versions = [{"kind": "local", "path": f"/v{i}.mp4"} for i in range(51)]
        body = _kj_pick_body(source_meta={"versions": versions})
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400

    def test_auto_approve_skipped_for_kj_pick(self, client, sing_app, token):
        """Even when auto-approve is on, kj_pick stays pending — the KJ must
        still pick a specific version before it joins the rotation."""
        sing_app.sing_store.set_auto_approve(True)
        resp = client.post(f"/sing/submit?t={token}", json=_kj_pick_body())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is False
        assert data["request"]["status"] == "pending"
        # No rotation entry yet.
        entries = sing_app.rotation.get_rotation()
        assert not any(e["singer"] == "Andrew" for e in entries)

    def test_other_source_types_still_auto_approve(self, client, sing_app, token):
        """Regression guard — the kj_pick skip must not break the normal
        auto-approve path for concrete-version sources."""
        sing_app.sing_store.set_auto_approve(True)
        body = _kj_pick_body(
            source_type="local",
            source_ref="/tmp/song.mp4",
            source_meta=None,
        )
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is True


def _req_with_versions(versions):
    """Build the minimum row shape ``_pick_version_from_kj_pick`` reads."""
    return {"source_meta": json.dumps({"versions": versions})}


class TestPickVersionFromKjPick:
    def test_picks_local(self):
        req = _req_with_versions([
            {"source": "local", "local": {"path": "/media/foo.mp4"}},
        ])
        assert _pick_version_from_kj_pick(req, 0) == ("local", "/media/foo.mp4", None)

    def test_picks_kn_with_divebar(self):
        req = _req_with_versions([
            {
                "source": "kn",
                "kn": {
                    "brand_code": "SF",
                    "divebar": {"file_id": "drive-123", "drive_path": "/SF/001.mp4"},
                    "youtube_url": "https://yt/x",
                },
            },
        ])
        src_type, src_ref, meta = _pick_version_from_kj_pick(req, 0)
        assert src_type == "divebar"
        assert src_ref == "drive-123"
        assert meta == {"brand_code": "SF", "disc_id": "/SF/001.mp4"}

    def test_picks_kn_youtube_only(self):
        req = _req_with_versions([
            {
                "source": "kn",
                "kn": {
                    "brand_code": "KV",
                    "youtube_url": "https://yt/abc",
                },
            },
        ])
        src_type, src_ref, meta = _pick_version_from_kj_pick(req, 0)
        assert src_type == "youtube"
        assert src_ref == "https://yt/abc"
        assert meta == {"brand_code": "KV"}

    def test_picks_specific_index_from_mixed_list(self):
        req = _req_with_versions([
            {"source": "local", "local": {"path": "/v0.mp4"}},
            {"source": "kn", "kn": {"youtube_url": "https://yt/v1"}},
            {"source": "local", "local": {"path": "/v2.mp4"}},
        ])
        assert _pick_version_from_kj_pick(req, 1)[0] == "youtube"
        assert _pick_version_from_kj_pick(req, 2) == ("local", "/v2.mp4", None)

    def test_missing_index_raises(self):
        req = _req_with_versions([{"source": "local", "local": {"path": "/x.mp4"}}])
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick(req, None)

    def test_out_of_range_raises(self):
        req = _req_with_versions([{"source": "local", "local": {"path": "/x.mp4"}}])
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick(req, 1)
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick(req, -1)

    def test_non_integer_index_raises(self):
        req = _req_with_versions([{"source": "local", "local": {"path": "/x.mp4"}}])
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick(req, "first")

    def test_corrupt_source_meta_raises(self):
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick({"source_meta": "{not-json"}, 0)

    def test_empty_source_meta_raises(self):
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick({"source_meta": None}, 0)

    def test_local_without_path_raises(self):
        req = _req_with_versions([{"source": "local", "local": {}}])
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick(req, 0)

    def test_kn_without_divebar_or_youtube_raises(self):
        req = _req_with_versions([{"source": "kn", "kn": {"brand_code": "XX"}}])
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick(req, 0)

    def test_unknown_source_raises(self):
        req = _req_with_versions([{"source": "spotify", "spotify": {"id": "x"}}])
        with pytest.raises(ValueError):
            _pick_version_from_kj_pick(req, 0)
