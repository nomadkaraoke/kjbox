import os
import types
import unicodedata

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
    dest = tmp_path / "NOMAD-720p"

    def fake_run(cmd, **kw):
        os.makedirs(dest, exist_ok=True)
        (dest / "NOMAD-0001 - A - B.mp4").write_bytes(b"x")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    fake_requests = types.SimpleNamespace(post=lambda url, **kw: posted.setdefault("url", url) or _Resp())
    out = sm.run_sync(_cfg(tmp_path), requests_lib=fake_requests)
    assert out["changed"] is True and out["rescanned"] is True and out["copied"] == 1
    # Must poke the app's internal bind port (app_bind_port default 5001), NOT the
    # public flask_port (80) that _cfg sets — posting to the proxy would 301 and miss /rescan.
    assert posted["url"] == "http://127.0.0.1:5001/rescan"


def test_run_sync_rescan_url_override(tmp_path, monkeypatch):
    posted = {}
    dest = tmp_path / "NOMAD-720p"

    def fake_run(cmd, **kw):
        os.makedirs(dest, exist_ok=True)
        (dest / "x.mp4").write_bytes(b"x")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    cfg = _cfg(tmp_path)
    cfg["master_sync_rescan_url"] = "http://127.0.0.1:9999/rescan"
    fake_requests = types.SimpleNamespace(post=lambda url, **kw: posted.setdefault("url", url) or _Resp())
    sm.run_sync(cfg, requests_lib=fake_requests)
    assert posted["url"] == "http://127.0.0.1:9999/rescan"


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
    assert out == {"changed": False, "copied": 0, "deleted": 0, "rescanned": False, "error": "disabled"}


# --- reconcile deletions (true-mirror; deletes/renames propagate) ---

def _ls_result(names):
    stdout = "\n".join(f"gs://bucket/prefix/{n}" for n in names)
    return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _local(dest, names):
    dest.mkdir(parents=True, exist_ok=True)
    for n in names:
        (dest / n).write_bytes(b"x")


def test_reconcile_deletes_local_absent_from_source(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, ["keep.mp4", "gone1.mp4", "gone2.mp4"])
    monkeypatch.setattr(sm.subprocess, "run", lambda cmd, **kw: _ls_result(["keep.mp4"]))
    n = sm._reconcile_deletions({}, "gs://bucket/prefix/", str(dest), {}, "gcloud")
    assert n == 2
    assert (dest / "keep.mp4").exists()
    assert not (dest / "gone1.mp4").exists()
    assert not (dest / "gone2.mp4").exists()


def test_reconcile_never_deletes_on_empty_listing(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, ["a.mp4", "b.mp4"])
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert sm._reconcile_deletions({}, "gs://x/", str(dest), {}, "gcloud") == 0
    assert (dest / "a.mp4").exists() and (dest / "b.mp4").exists()


def test_reconcile_never_deletes_on_failed_listing(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, ["a.mp4"])
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="gs://x/z.mp4", stderr="boom"))
    assert sm._reconcile_deletions({}, "gs://x/", str(dest), {}, "gcloud") == 0
    assert (dest / "a.mp4").exists()


def test_reconcile_ls_exception_never_deletes(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, ["a.mp4"])

    def boom(cmd, **kw):
        raise RuntimeError("network")

    monkeypatch.setattr(sm.subprocess, "run", boom)
    assert sm._reconcile_deletions({}, "gs://x/", str(dest), {}, "gcloud") == 0
    assert (dest / "a.mp4").exists()


def test_reconcile_refuses_over_cap(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, [f"local{i}.mp4" for i in range(5)])
    # Source has none of the local files -> 5 would be deleted, over the cap of 2.
    monkeypatch.setattr(sm.subprocess, "run", lambda cmd, **kw: _ls_result(["other.mp4"]))
    assert sm._reconcile_deletions({"master_sync_max_deletes": 2}, "gs://x/", str(dest), {}, "gcloud") == 0
    for i in range(5):
        assert (dest / f"local{i}.mp4").exists()


def test_reconcile_dry_run_logs_but_keeps(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, ["gone.mp4"])
    monkeypatch.setattr(sm.subprocess, "run", lambda cmd, **kw: _ls_result(["keep.mp4"]))
    assert sm._reconcile_deletions({"master_sync_delete_dry_run": True}, "gs://x/", str(dest), {}, "gcloud") == 0
    assert (dest / "gone.mp4").exists()


def test_reconcile_kill_switch_off_does_not_even_list(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, ["gone.mp4"])
    called = {"ran": False}

    def fake(cmd, **kw):
        called["ran"] = True
        return _ls_result([])

    monkeypatch.setattr(sm.subprocess, "run", fake)
    assert sm._reconcile_deletions({"master_sync_delete_removed": False}, "gs://x/", str(dest), {}, "gcloud") == 0
    assert called["ran"] is False
    assert (dest / "gone.mp4").exists()


def test_reconcile_ignores_unicode_normalization_difference(tmp_path, monkeypatch):
    # Local file stored NFD (decomposed accents); GCS lists the SAME name NFC.
    # These differ byte-wise but are the same logical master -> must NOT be deleted
    # (else it churns: delete then rsync re-downloads every run). Real device case:
    # NOMAD-0990 "Curicó", NOMAD-1027 "Beyoncé", NOMAD-1207 "Việt Nhân", etc.
    logical = "NOMAD-0990 - Kiltro - Curicó.mp4"
    nfd_name = unicodedata.normalize("NFD", logical)
    nfc_name = unicodedata.normalize("NFC", logical)
    assert nfd_name != nfc_name  # sanity: the two forms really do differ on disk
    dest = tmp_path / "NOMAD-720p"
    _local(dest, [nfd_name])
    monkeypatch.setattr(sm.subprocess, "run", lambda cmd, **kw: _ls_result([nfc_name]))
    assert sm._reconcile_deletions({}, "gs://x/", str(dest), {}, "gcloud") == 0
    assert (dest / nfd_name).exists()


def test_run_sync_reconciles_and_rescans_on_delete(tmp_path, monkeypatch):
    dest = tmp_path / "NOMAD-720p"
    _local(dest, ["stale.mp4"])  # present locally, absent upstream
    posted = {}

    def fake(cmd, **kw):
        if "rsync" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")  # nothing new to copy
        if "ls" in cmd:
            return _ls_result(["keep.mp4"])  # source no longer has stale.mp4
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake)
    fake_requests = types.SimpleNamespace(post=lambda url, **kw: posted.setdefault("url", url) or _Resp())
    out = sm.run_sync(_cfg(tmp_path), requests_lib=fake_requests)
    assert out["deleted"] == 1
    assert not (dest / "stale.mp4").exists()
    assert out["changed"] is True and out["rescanned"] is True
    assert posted["url"] == "http://127.0.0.1:5001/rescan"


def test_run_sync_reports_gcloud_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    out = sm.run_sync(_cfg(tmp_path), requests_lib=None)
    assert out["error"] and out["changed"] is False


def test_run_sync_gcloud_failure_empty_stderr_still_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr=""))
    out = sm.run_sync(_cfg(tmp_path), requests_lib=None)
    assert out["error"] and out["changed"] is False
