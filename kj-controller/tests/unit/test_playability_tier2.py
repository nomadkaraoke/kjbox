"""Unit tests for tier-2 async render verification (routes._run_tier2_check
+ _enqueue_tier2 + worker plumbing).

Tier-1 (the inline gate) hard-blocks on integrity+decode. Tier-2 runs the
expensive *render proof* against the ACTIVE renderer off the request path and
stamps a playability_warning on the rotation entry when the live renderer
can't actually render the linked file.
"""

import queue as _queue_mod
import types
from unittest.mock import MagicMock, patch

import routes


def _fake_app(render_mode="mpv"):
    store = MagicMock()
    app = types.SimpleNamespace(
        vlc=types.SimpleNamespace(render_mode=render_mode),
        kj_config={},
        rotation=types.SimpleNamespace(store=store),
    )
    return app, store


class TestRunTier2Check:
    def test_flags_entry_on_render_failure(self):
        app, store = _fake_app("mpv")
        checker = MagicMock()
        checker.check.return_value = types.SimpleNamespace(
            verdict={"overall_ok": False,
                     "reasons": ["mpv: no video frame rendered"]}
        )
        routes._run_tier2_check(app, 5, "/x/v.mp4", checker=checker)
        store.set_playability_warning.assert_called_once()
        entry_id, msg = store.set_playability_warning.call_args.args
        assert entry_id == 5
        assert "no video frame rendered" in msg

    def test_clears_warning_on_success(self):
        app, store = _fake_app()
        checker = MagicMock()
        checker.check.return_value = types.SimpleNamespace(
            verdict={"overall_ok": True, "reasons": []}
        )
        routes._run_tier2_check(app, 5, "/x/v.mp4", checker=checker)
        store.set_playability_warning.assert_called_once_with(5, None)

    def test_uses_active_renderer(self):
        app, store = _fake_app("vlc")
        checker = MagicMock()
        checker.check.return_value = types.SimpleNamespace(
            verdict={"overall_ok": True, "reasons": []}
        )
        routes._run_tier2_check(app, 1, "/x/v.mkv", checker=checker)
        assert checker.check.call_args.kwargs["renderers"] == ("vlc",)

    def test_skips_pure_audio(self):
        app, store = _fake_app()
        checker = MagicMock()
        routes._run_tier2_check(app, 5, "/x/a.mp3", checker=checker)
        checker.check.assert_not_called()
        store.set_playability_warning.assert_not_called()

    def test_swallows_exceptions(self):
        app, store = _fake_app()
        checker = MagicMock()
        checker.check.side_effect = RuntimeError("boom")
        # Must not raise — tier-2 is best-effort and never breaks the link flow.
        routes._run_tier2_check(app, 5, "/x/v.mp4", checker=checker)
        store.set_playability_warning.assert_not_called()


class TestEnqueueTier2:
    def test_queues_render_relevant_file(self, monkeypatch):
        q = _queue_mod.Queue()
        monkeypatch.setattr(routes, "_tier2_queue", q)
        monkeypatch.setattr(routes, "_tier2_worker_started", True)  # no worker
        app = object()  # sentinel app threaded through per-task
        routes._enqueue_tier2(app, 7, "/x/v.mp4")
        assert q.qsize() == 1
        assert q.get_nowait() == (app, 7, "/x/v.mp4")

    def test_skips_pure_audio(self, monkeypatch):
        q = _queue_mod.Queue()
        monkeypatch.setattr(routes, "_tier2_queue", q)
        monkeypatch.setattr(routes, "_tier2_worker_started", True)
        routes._enqueue_tier2(MagicMock(), 7, "/x/a.mp3")
        assert q.qsize() == 0

    def test_worker_drains_queue_and_runs_check(self, monkeypatch):
        q = _queue_mod.Queue()
        monkeypatch.setattr(routes, "_tier2_queue", q)
        monkeypatch.setattr(routes, "_tier2_worker_started", False)
        called = []
        monkeypatch.setattr(
            routes, "_run_tier2_check",
            lambda app, eid, path, **kw: called.append((app, eid, path)),
        )
        app = object()
        routes._enqueue_tier2(app, 9, "/x/v.mp4")
        q.join()  # blocks until the worker calls task_done()
        # The worker must use the app threaded through *this* task, not one
        # captured at thread start.
        assert called == [(app, 9, "/x/v.mp4")]
