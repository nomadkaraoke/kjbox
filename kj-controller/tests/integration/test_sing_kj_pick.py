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

    def test_accepts_popular_song_over_trim_cap(self):
        # 2026-09-24: "I Want It That Way" had 60 versions and every singer who
        # tapped it got a 400 — oversized snapshots are trimmed, not refused.
        versions = [{"kind": "local", "path": f"/v{i}.mp4"} for i in range(60)]
        err = _validate_kj_pick_payload({"source_meta": {"versions": versions}})
        assert err is None

    def test_rejects_pathological_version_count(self):
        versions = [{"kind": "local", "path": f"/v{i}.mp4"} for i in range(1001)]
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

    def test_trims_oversized_snapshot_to_best_ranked(self, client, sing_app, token):
        """60 versions (a popular song) → accepted, trimmed to the 50 best by
        priority_rank; the best version survives, the worst ones drop."""
        from sing import _KJ_PICK_MAX_VERSIONS

        # Unbranded filler first, the single best-ranked (SC) version LAST so a
        # naive head-truncation would lose it.
        versions = [{"source": "local", "local": {"path": f"/v{i}.mp4", "disc_id": None}}
                    for i in range(59)]
        versions.append({"source": "local",
                         "local": {"path": "/best.zip", "disc_id": "SC8542-01"}})
        body = _kj_pick_body(source_meta={"versions": versions, "version_count": 60})
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        stored = sing_app.sing_store.get_request(resp.get_json()["request"]["id"])
        kept = json.loads(stored["source_meta"])["versions"]
        assert len(kept) == _KJ_PICK_MAX_VERSIONS
        assert "/best.zip" in [v["local"]["path"] for v in kept]

    def test_rejects_pathological_snapshot(self, client, token):
        versions = [{"kind": "local", "path": f"/v{i}.mp4"} for i in range(1001)]
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


class TestAutoPickMatchesKjBest:
    """Auto-pick must choose the SAME winner the KJ-side rotation-link search
    marks with its gold "Best" pill. Both sides rank through
    ``version_priority.rank_version`` with the same config, so the version with
    the lowest rank in the snapshot is the one auto-pick binds.
    """

    # A realistic mixed snapshot, deliberately shuffled so no ordering
    # assumption can pass by accident:
    #   idx 0 — commercial KN, YouTube only (KV)          → commercial tier
    #   idx 1 — local library file, NOMAD disc id          → community tier
    #   idx 2 — community KN with a Divebar cross-ref (CC) → community tier, top brand
    #   idx 3 — commercial KN, YouTube only (unknown code) → commercial-unknown tier
    def _versions(self):
        return [
            {"source": "kn", "kn": {"brand_code": "KV", "is_community": False,
                                    "youtube_url": "https://yt/kv"}},
            {"source": "local", "local": {"path": "/media/NOMAD-0001 - A - B.mp4",
                                          "disc_id": "NOMAD-0001",
                                          "filename": "NOMAD-0001 - A - B.mp4"}},
            {"source": "kn", "kn": {"brand_code": "CC", "is_community": True,
                                    "divebar": {"file_id": "dv-cc-1",
                                                "drive_path": "CC/track.zip"},
                                    "youtube_url": "https://yt/cc"}},
            {"source": "kn", "kn": {"brand_code": "XYZQ", "is_community": False,
                                    "youtube_url": "https://yt/xyzq"}},
        ]

    def test_ranked_order_matches_rank_version(self):
        """The index ordering mirrors a direct rank_version sort — the same
        computation the admin picker and rotation-link search use."""
        import copy
        import version_priority
        from routes import _ranked_version_indices

        cfg = {}
        versions = self._versions()
        expected = sorted(
            range(len(versions)),
            key=lambda i: version_priority.rank_version(
                copy.deepcopy(versions[i]), cfg))
        assert _ranked_version_indices(copy.deepcopy(versions), cfg) == expected
        # And concretely: CC (community, top priority, divebar) wins; the
        # unknown-brand commercial YouTube row comes last.
        assert expected[0] == 2
        assert expected[-1] == 3

    def test_resolve_binds_the_rank_winner(self, client, sing_app, token):
        """End to end through /sing/submit with auto-approve ON: the bound
        source is the CC Divebar file, not the first-listed version."""
        from unittest.mock import patch

        sing_app.sing_store.set_auto_approve(True)
        body = _kj_pick_body(source_meta={"versions": self._versions()})
        with patch("routes.divebar.get_download_url",
                   return_value="https://dl/cc-1.zip"), \
                patch("routes._download_worker"):
            resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is True
        stored = sing_app.sing_store.get_request(data["request"]["id"])
        assert stored["source_type"] == "divebar"
        assert stored["source_ref"] == "dv-cc-1"

    def test_kj_config_priority_override_respected(self):
        """A KJ re-ordering kn_priority_community flips the winner — proving
        auto-pick reads the same config keys as the KJ-side ranking."""
        import version_priority
        from routes import _ranked_version_indices

        cfg = {"kn_priority_community": ["NOMAD", "CC"]}
        versions = self._versions()
        order = _ranked_version_indices(versions, cfg)
        # NOMAD local now outranks CC divebar.
        assert order[0] == 1
        assert order[1] == 2
        assert version_priority.rank_version(versions[1], cfg) < \
            version_priority.rank_version(versions[2], cfg)
