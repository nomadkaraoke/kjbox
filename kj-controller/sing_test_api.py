"""Local-stack test API: ``/sing/api/test/*`` (spec §8.1, task A12).

Lets the mobile app's Maestro flows (``stack.js`` ``ACTION: kjbox``) drive a
real kjbox process the way a host would: reset to the fixture night, seed
singers, advance the rotation, mark songs Done/undone, flip night flags, add or
remove host entries, tune rate limits, drain the history outbox, and read the
whole night back.

A12-R2: mounted ONLY when ``KJBOX_TEST_API=1`` AND the server binds a loopback
address (``install_test_api(app, bind_host)`` decides; ``dev_server.py`` calls
it). Otherwise no route exists, so every path is a 404. The public sing host
never reaches it either (the host guard 404s it).

Every endpoint is JSON. Errors are ``{"error": <code>, "message"?}`` with codes
``invalid`` (400) and ``not_found`` (404).
"""

import ipaddress
import os

from flask import Blueprint, current_app, jsonify, request

import localstack

TEST_API_ENV = "KJBOX_TEST_API"
TEST_PREFIX = "/sing/api/test"

sing_test_api_bp = Blueprint("sing_test_api", __name__, url_prefix=TEST_PREFIX)


def is_loopback(host):
    """True for 127.0.0.0/8, ::1 and "localhost"; False for anything else,
    including the wildcard binds (0.0.0.0, ::) and LAN addresses."""
    if not host:
        return False
    host = str(host).strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install_test_api(app, bind_host, environ=None):
    """Register the test API iff KJBOX_TEST_API=1 and ``bind_host`` is loopback.
    Returns whether it was registered."""
    env = environ if environ is not None else os.environ
    if env.get(TEST_API_ENV) != "1" or not is_loopback(bind_host):
        return False
    app.register_blueprint(sing_test_api_bp)
    return True


_PROXY_HEADERS = ("X-Forwarded-For", "CF-Connecting-IP", "X-Real-IP", "Forwarded")


@sing_test_api_bp.before_request
def _loopback_clients_only():
    # Defence in depth: the bind is loopback, so every client is too, and a
    # request that came through a proxy or tunnel (it carries a forwarding
    # header) is never served, even if the proxy itself is local.
    if not is_loopback(request.remote_addr or "127.0.0.1"):
        return _err("not_found", 404)
    if any(h in request.headers for h in _PROXY_HEADERS):
        return _err("not_found", 404)
    return None


def _err(code, status, message=None):
    body = {"error": code}
    if message:
        body["message"] = message
    return jsonify(body), status


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _app():
    return current_app._get_current_object()


def _entry_or_404(entry_id):
    if not isinstance(entry_id, int) or isinstance(entry_id, bool):
        return None, _err("invalid", 400, "entry_id (an integer) is required")
    entry = current_app.rotation.store.get_entry(entry_id)
    if entry is None:
        return None, _err("not_found", 404, f"no entry {entry_id}")
    return entry, None


@sing_test_api_bp.route("/reset", methods=["POST"])
def reset():
    """``{fixture?: "default" | "empty"}`` → a new night under code 2121.
    ``default`` seeds the fixture night's start rotation and settings."""
    name = _body().get("fixture", "default")
    if name == "default":
        # The fixture the server was started with (dev_server --fixture).
        fixture = localstack.load_fixture(getattr(_app(), "localstack_fixture_path", None))
    elif name == "empty":
        fixture = None
    else:
        return _err("invalid", 400, "fixture must be 'default' or 'empty'")
    refs = localstack.reset_night(_app(), fixture)
    return jsonify({"ok": True, "entries": refs, "state": localstack.night_state(_app())})


@sing_test_api_bp.route("/seed", methods=["POST"])
def seed():
    singers = _body().get("singers")
    if not isinstance(singers, list) or not singers:
        return _err("invalid", 400, "singers (a non-empty list) is required")
    try:
        out = localstack.seed_singers(_app(), singers)
    except (ValueError, TypeError, AttributeError) as e:
        return _err("invalid", 400, str(e))
    return jsonify({"ok": True, "singers": out})


@sing_test_api_bp.route("/advance", methods=["POST"])
def advance():
    return jsonify({"ok": True, **localstack.advance(_app())})


@sing_test_api_bp.route("/done", methods=["POST"])
def done():
    entry_id = _body().get("entry_id")
    if entry_id is None:
        cur = localstack.current_entry(_app())
        if cur is None:
            return _err("not_found", 404, "nobody is singing")
        entry_id = cur["id"]
    _, err = _entry_or_404(entry_id)
    if err:
        return err
    current_app.rotation.update_status(entry_id, "Done")
    return jsonify({"ok": True, "entry": current_app.rotation.store.get_entry(entry_id)})


@sing_test_api_bp.route("/undone", methods=["POST"])
def undone():
    entry_id = _body().get("entry_id")
    _, err = _entry_or_404(entry_id)
    if err:
        return err
    current_app.rotation.update_status(entry_id, "Waiting")
    return jsonify({"ok": True, "entry": current_app.rotation.store.get_entry(entry_id)})


@sing_test_api_bp.route("/flags", methods=["POST"])
def flags():
    body = _body()
    unknown = set(body) - set(localstack.DEFAULT_FLAGS)
    if unknown:
        return _err("invalid", 400, f"unknown flags: {sorted(unknown)}")
    if any(not isinstance(v, bool) for v in body.values()):
        return _err("invalid", 400, "flag values must be booleans")
    return jsonify({"ok": True, "flags": localstack.set_flags(_app(), body)})


@sing_test_api_bp.route("/host-add", methods=["POST"])
def host_add():
    body = _body()
    name = (body.get("name") or "").strip()
    title = (body.get("title") or "").strip()
    artist = (body.get("artist") or "").strip()
    if not name or not title:
        return _err("invalid", 400, "name and title are required")
    entry = current_app.rotation.add_entry(name, localstack.song_text(artist, title))
    return jsonify({"ok": True, "entry": entry})


@sing_test_api_bp.route("/host-remove", methods=["POST"])
def host_remove():
    """``{entry_id, delete?}``: the host cancels the entry (it stays on the KJ
    screen as Cancelled; the singer's app shows it removed), or with
    ``delete: true`` deletes it outright."""
    body = _body()
    entry_id = body.get("entry_id")
    _, err = _entry_or_404(entry_id)
    if err:
        return err
    if body.get("delete") is True:
        current_app.rotation.delete_entry(entry_id)
        return jsonify({"ok": True, "deleted": True, "entry_id": entry_id})
    current_app.rotation.update_status(entry_id, "Cancelled")
    return jsonify({"ok": True, "deleted": False,
                    "entry": current_app.rotation.store.get_entry(entry_id)})


@sing_test_api_bp.route("/rate-limits", methods=["POST"])
def rate_limits():
    """``{per_device?, per_ip?}`` (positive ints) → the web/v1 singer budgets
    per 5-minute window; the current windows are cleared either way."""
    body = _body()
    keys = {"per_device": "sing_rate_limit_per_device", "per_ip": "sing_rate_limit_per_ip"}
    for k in body:
        if k not in keys:
            return _err("invalid", 400, f"unknown field {k}")
        v = body[k]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            return _err("invalid", 400, f"{k} must be a positive integer")
    cfg = current_app.kj_config
    for k, v in body.items():
        cfg[keys[k]] = v
    localstack._clear_rate_limits()
    return jsonify({"ok": True, "rate_limits": {
        "per_device": int(cfg.get("sing_rate_limit_per_device") or 8),
        "per_ip": int(cfg.get("sing_rate_limit_per_ip") or 60),
    }})


@sing_test_api_bp.route("/outbox/drain", methods=["POST"])
def outbox_drain():
    return jsonify({"ok": True, "sent": localstack.drain_outbox(_app())})


@sing_test_api_bp.route("/state", methods=["GET"])
def state():
    return jsonify(localstack.night_state(_app()))
