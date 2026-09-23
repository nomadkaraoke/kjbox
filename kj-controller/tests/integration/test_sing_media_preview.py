"""Tests for the singer-facing version-details + preview endpoints.

The public host (sing.nomadkaraoke.com) blocks every non-sing route, so the
singer version picker gets its own token-gated delegates:

  * ``GET  /sing/lib/<name>``        — whitelisted KJ-static assets (preview
    player, CDG renderer, hls.js) for the preview modal.
  * ``POST /sing/media-info``        — ffprobe spec sheet for a library file,
    with the on-disk path withheld from the response.
  * ``POST /sing/preview/resolve``   — PreviewService delegate, restricted to
    the descriptor sources a singer can reach (local/divebar/youtube) and
    per-IP rate limited.
"""

import os

import pytest


class TestLibFiles:
    def test_serves_whitelisted_asset(self, client, token):
        resp = client.get(f"/sing/lib/preview.js?t={token}")
        assert resp.status_code == 200
        assert b"openPreview" in resp.data

    def test_serves_cdg_and_hls(self, client, token):
        for name in ("cdg.js", "hls.min.js"):
            resp = client.get(f"/sing/lib/{name}?t={token}")
            assert resp.status_code == 200, name

    def test_rejects_unknown_name(self, client, token):
        resp = client.get(f"/sing/lib/app.js?t={token}")
        assert resp.status_code == 404

    def test_rejects_traversal_shapes(self, client, token):
        # Anything not in the whitelist 404s — including traversal attempts.
        resp = client.get(f"/sing/lib/..%2Fapp.py?t={token}")
        assert resp.status_code == 404

    def test_requires_token(self, client):
        resp = client.get("/sing/lib/preview.js")
        assert resp.status_code == 403


class TestMediaInfo:
    def test_requires_token(self, client):
        resp = client.post("/sing/media-info", json={"file_path": "/x.mp4"})
        assert resp.status_code == 403

    def test_requires_file_path(self, client, token):
        resp = client.post(f"/sing/media-info?t={token}", json={})
        assert resp.status_code == 400

    def test_rejects_path_outside_allowed_roots(self, client, token):
        resp = client.post(
            f"/sing/media-info?t={token}", json={"file_path": "/etc/passwd"})
        assert resp.status_code == 404

    def test_probes_allowed_file_and_hides_path(self, client, sing_app, token, tmp_path):
        # Point path validation at a real file inside an allowed media folder.
        media_dir = sing_app.media.config.get("media_folders", [None])[0] \
            if isinstance(getattr(sing_app.media, "config", None), dict) else None
        target = None
        if media_dir and os.path.isdir(media_dir):
            target = os.path.join(media_dir, "probe-me.zip")
            with open(target, "wb") as fh:
                fh.write(b"PK\x03\x04fakezip")
        if not target:
            pytest.skip("no writable media folder in test config")
        resp = client.post(
            f"/sing/media-info?t={token}", json={"file_path": target})
        assert resp.status_code == 200
        info = resp.get_json()
        # A fake zip fails the probe gracefully OR describes itself — either
        # way the server path must not leak.
        assert "path" not in info
        assert info.get("filename") == "probe-me.zip"


class TestPreviewResolve:
    def test_requires_token(self, client):
        resp = client.post("/sing/preview/resolve", json={"source": "local"})
        assert resp.status_code == 403

    def test_rejects_unsupported_source(self, client, token):
        for source in (None, "make", "kj_pick", "weird"):
            resp = client.post(
                f"/sing/preview/resolve?t={token}", json={"source": source})
            assert resp.status_code == 400, source

    def test_delegates_to_preview_service(self, client, sing_app, token):
        class FakePreview:
            def resolve(self, descriptor):
                assert descriptor["source"] == "youtube"
                return {"mode": "youtube", "youtube_url": descriptor["youtube_url"],
                        "title": "", "format": "YouTube", "ext": ""}

        old = getattr(sing_app, "preview", None)
        sing_app.preview = FakePreview()
        try:
            resp = client.post(
                f"/sing/preview/resolve?t={token}",
                json={"source": "youtube", "youtube_url": "https://youtu.be/x"})
        finally:
            if old is None:
                del sing_app.preview
            else:
                sing_app.preview = old
        assert resp.status_code == 200
        assert resp.get_json()["mode"] == "youtube"

    def test_rate_limited(self, client, sing_app, token):
        import sing as sing_mod

        sing_mod._preview_rate_limit_state.clear()
        sing_app.kj_config["sing_preview_rate_limit"] = 2
        sing_app.kj_config["sing_preview_rate_window_s"] = 60

        class FakePreview:
            def resolve(self, descriptor):
                return {"mode": "youtube", "youtube_url": "u", "title": "",
                        "format": "YouTube", "ext": ""}

        old = getattr(sing_app, "preview", None)
        sing_app.preview = FakePreview()
        try:
            codes = []
            for _ in range(3):
                resp = client.post(
                    f"/sing/preview/resolve?t={token}",
                    json={"source": "youtube", "youtube_url": "https://youtu.be/x"})
                codes.append(resp.status_code)
        finally:
            sing_mod._preview_rate_limit_state.clear()
            sing_app.kj_config.pop("sing_preview_rate_limit", None)
            sing_app.kj_config.pop("sing_preview_rate_window_s", None)
            if old is None:
                del sing_app.preview
            else:
                sing_app.preview = old
        assert codes == [200, 200, 429]


class TestPreviewStreamDelegates:
    def test_stream_requires_token(self, client):
        assert client.get("/sing/preview/stream/tok123").status_code == 403

    def test_stream_unknown_token_404s(self, client, token):
        resp = client.get(f"/sing/preview/stream/nope?t={token}")
        assert resp.status_code == 404

    def test_cdg_unknown_token_404s(self, client, token):
        resp = client.get(f"/sing/preview/cdg/nope/audio?t={token}")
        assert resp.status_code == 404

    def test_hls_unknown_token_404s(self, client, token):
        resp = client.get(f"/sing/preview/hls/nope/index.m3u8?t={token}")
        assert resp.status_code == 404
