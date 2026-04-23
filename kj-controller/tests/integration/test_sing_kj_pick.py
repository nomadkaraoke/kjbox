"""Tests for the ``kj_pick`` source_type (Phase A).

A ``kj_pick`` request captures the singer's candidate-version snapshot and
defers the actual version selection to the KJ at approval time. These tests
lock down:

  * The ``_validate_kj_pick_payload`` helper rejects malformed snapshots.
  * ``/sing/submit`` accepts well-formed kj_pick bodies and persists the
    versions into ``source_meta``.
  * ``/sing/submit`` rejects kj_pick bodies that are missing the required
    song_artist / song_title (we still need a display label in the admin UI).
  * Auto-approve is skipped for kj_pick — binding a version must go through
    the KJ, otherwise the rotation entry would have no file attached.
"""

from sing import _validate_kj_pick_payload


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
        import json
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
