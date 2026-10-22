"""Local-stack guards: the data dir, config overrides and the debugger (A12 fix 1)."""

import json
import os
import subprocess

import pytest

import localstack

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(APP_DIR, "scripts", "local-stack.sh")


# --- data dir --------------------------------------------------------------------

def test_claim_new_or_empty_dir_writes_the_marker(tmp_path):
    new = tmp_path / "kjdata-localstack"
    assert localstack.claim_data_dir(str(new)) == os.path.realpath(new)
    assert (new / localstack.MARKER).exists()
    empty = tmp_path / "other-localstack"
    empty.mkdir()
    localstack.claim_data_dir(str(empty))
    assert (empty / localstack.MARKER).exists()


def test_claim_accepts_a_marked_dir_with_data(tmp_path):
    d = tmp_path / "kjdata-localstack"
    d.mkdir()
    (d / localstack.MARKER).write_text("")
    (d / "rotation.db").write_text("x")
    localstack.claim_data_dir(str(d))


def test_claim_refuses_unmarked_non_empty_dir(tmp_path):
    d = tmp_path / "kjdata-localstack"
    d.mkdir()
    (d / "precious.txt").write_text("keep me")
    with pytest.raises(ValueError, match="marker"):
        localstack.claim_data_dir(str(d))
    assert not (d / localstack.MARKER).exists()


@pytest.mark.parametrize("name", ["kjdata", "data", "Documents", "local-stack"])
def test_claim_refuses_names_without_localstack(tmp_path, name):
    with pytest.raises(ValueError, match="localstack"):
        localstack.claim_data_dir(str(tmp_path / name))


def test_claim_refuses_home_and_kjdata(monkeypatch, tmp_path):
    home = tmp_path / "home-localstack"
    (home / "kjdata").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(ValueError):
        localstack.claim_data_dir(str(home))
    with pytest.raises(ValueError):
        localstack.claim_data_dir(str(home / "kjdata" / "x-localstack"))
    with pytest.raises(ValueError):
        localstack.claim_data_dir("")


def _script(cmd, data_dir):
    env = dict(os.environ, KJBOX_LOCALSTACK_DIR=str(data_dir),
               KJBOX_LOCALSTACK_PORT="18999")
    return subprocess.run(["bash", SCRIPT, cmd], env=env, capture_output=True,
                          text=True, timeout=30)


@pytest.mark.parametrize("cmd", ["reset", "up", "serve"])
def test_script_refuses_unmarked_non_empty_dir(tmp_path, cmd):
    d = tmp_path / "my-localstack"
    d.mkdir()
    (d / "precious.txt").write_text("keep me")
    proc = _script(cmd, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "marker" in proc.stderr
    assert (d / "precious.txt").read_text() == "keep me"


@pytest.mark.parametrize("cmd", ["reset", "up"])
def test_script_refuses_names_without_localstack(tmp_path, cmd):
    d = tmp_path / "Documents"
    d.mkdir()
    (d / ".kjbox-localstack").write_text("")
    (d / "precious.txt").write_text("keep me")
    proc = _script(cmd, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "localstack" in proc.stderr
    assert (d / "precious.txt").exists()


# --- config overrides ----------------------------------------------------------------

def test_overrides_allow_list():
    env = {"KJ_CONFIG_OVERRIDES": json.dumps({
        "gen_api_url": "http://127.0.0.1:9010", "decide_kjbox_secret": "s",
        "sing_rate_limit_per_device": 3})}
    assert localstack.config_overrides_from_env(env)["gen_api_url"] == "http://127.0.0.1:9010"
    for bad in ({"rotation_db_path": "/Users/x/kjdata/rotation.db"},
                {"enable_vlc": True}, {"rotation_sheet_id": "abc"}):
        with pytest.raises(ValueError, match="not allowed"):
            localstack.config_overrides_from_env({"KJ_CONFIG_OVERRIDES": json.dumps(bad)})
    with pytest.raises(ValueError):
        localstack.config_overrides_from_env({"KJ_CONFIG_OVERRIDES": "[1]"})
    assert localstack.config_overrides_from_env({}) == {}


def test_hermetic_config_rejects_and_reasserts(tmp_path):
    d = tmp_path / "kjdata-localstack"
    d.mkdir()
    # The stack's own config.json can't switch hermetic settings back on.
    (d / "config.json").write_text(json.dumps({
        "enable_vlc": True, "divebar_api_url": "https://real.example",
        "rotation_db_path": "/somewhere/else.db", "night_capture_enabled": True,
        "rotation_sheet_id": "sheet"}))
    cfg = localstack.hermetic_config(str(d), {"gen_api_url": "http://127.0.0.1:9010"})
    assert cfg["gen_api_url"] == "http://127.0.0.1:9010"
    assert cfg["enable_vlc"] is False and cfg["divebar_api_url"] == ""
    assert cfg["night_capture_enabled"] is False and "rotation_sheet_id" not in cfg
    assert cfg["rotation_db_path"] == str(d / "rotation.db")
    with pytest.raises(ValueError, match="not allowed"):
        localstack.hermetic_config(str(d), {"media_db_path": "/tmp/x.db"})


# --- debugger -------------------------------------------------------------------

@pytest.mark.parametrize("host,debug", [("127.0.0.1", True), ("localhost", True),
                                        ("::1", True), ("0.0.0.0", False),
                                        ("192.168.8.10", False), ("", False)])
def test_dev_server_debug_only_on_loopback(host, debug):
    import dev_server
    assert dev_server._is_loopback(host) is debug
