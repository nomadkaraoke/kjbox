import types
import scripts.sync_masters as sm


class _Resp:
    status_code = 200


def _cfg(tmp_path):
    return {
        "master_sync_source": "gs://bucket/prefix/",
        "master_sync_dest": str(tmp_path / "NOMAD-720p"),
        "master_sync_credentials_file": str(tmp_path / "sa.json"),
        "master_sync_enabled": True,
        "flask_port": 80,
    }


def test_run_sync_triggers_rescan_when_files_copied(tmp_path, monkeypatch):
    posted = {}

    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout="Copying gs://bucket/prefix/x.mp4\n", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    fake_requests = types.SimpleNamespace(post=lambda url, **kw: posted.setdefault("url", url) or _Resp())
    out = sm.run_sync(_cfg(tmp_path), requests_lib=fake_requests)
    assert out["changed"] is True and out["rescanned"] is True
    assert posted["url"].endswith("/rescan")


def test_run_sync_no_change_skips_rescan(tmp_path, monkeypatch):
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    called = {"posted": False}
    fake_requests = types.SimpleNamespace(post=lambda *a, **k: called.__setitem__("posted", True))
    out = sm.run_sync(_cfg(tmp_path), requests_lib=fake_requests)
    assert out["changed"] is False and out["rescanned"] is False
    assert called["posted"] is False


def test_run_sync_disabled_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["master_sync_enabled"] = False
    out = sm.run_sync(cfg, requests_lib=None)
    assert out == {"changed": False, "copied": 0, "rescanned": False, "error": "disabled"}


def test_run_sync_reports_gcloud_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    out = sm.run_sync(_cfg(tmp_path), requests_lib=None)
    assert out["error"] and out["changed"] is False
