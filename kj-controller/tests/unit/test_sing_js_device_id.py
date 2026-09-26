"""Every singer-UI mutation must send ``device_id`` (2026-09-24 real night).

Behind cloudflared every phone at the venue shares ONE public IP. A POST without
``device_id`` only has the venue-wide per-IP rate-limit budget, so one tappy
singer can 429 the whole bar (photo-consent did exactly that), and features
keyed by device (push subs for phone-less singers) silently break.

This scans ``static-sing/sing.js`` for every POST and fails when a new one
omits ``device_id`` — add it, or add the path to ``EXEMPT`` with a reason.
"""

import re
from pathlib import Path

SING_JS = Path(__file__).resolve().parents[2] / "static-sing" / "sing.js"

EXEMPT = {
    "validate": "event-code entry before the SPA (and its device id) loads",
    "media-info": "read-only file details (POST only to carry a path); not rate-limited",
    "submit": "body is the `payload` object — asserted separately below",
}

_POST_CALL = re.compile(
    r"fetch(?:Json)?\(`\$\{BASE\}/(?P<path>[^`?]+)[^`]*`,\s*\{(?P<opts>.*?)\n\s*\}\)",
    re.S,
)


def _post_calls():
    src = SING_JS.read_text()
    calls = [(m.group("path"), m.group("opts")) for m in _POST_CALL.finditer(src)
             if 'method: "POST"' in m.group("opts")]
    return src, calls


def _sends_device_id(opts):
    code = re.sub(r"//[^\n]*", "", opts)   # a comment mentioning it doesn't count
    return re.search(r"\bdevice_id\s*:", code) is not None


def _route(path):
    return re.sub(r"\$\{[^}]+\}", "<id>", path)


def test_scanner_finds_the_known_singer_posts():
    _, calls = _post_calls()
    routes = {_route(p) for p, _ in calls}
    assert {"photo-consent", "push/subscribe", "rename", "requests/reorder",
            "update-phone"} <= routes, routes


def test_every_singer_post_sends_device_id():
    _, calls = _post_calls()
    missing = sorted({_route(p) for p, opts in calls
                      if _route(p) not in EXEMPT and not _sends_device_id(opts)})
    assert not missing, f"singer POSTs without device_id: {missing}"


def test_submit_payload_sends_device_id():
    src, _ = _post_calls()
    m = re.search(r"const payload = \{(.*?)\n\s*\};", src, re.S)
    assert m and "device_id: DEVICE_ID" in m.group(1)
