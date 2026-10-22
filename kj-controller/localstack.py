"""kjbox local stack: hermetic config, the fixture night, its catalog, reset and replay.

Used by ``dev_server.py --fixture`` (what ``scripts/local-stack.sh`` runs on
127.0.0.1:8040), the ``/sing/api/test/*`` blueprint (``sing_test_api.py``),
``scripts/build_localstack_fixture.py`` and the night-replay test.

A12-R1: nothing here may touch the real box or real singer data. Everything
lives under ``~/kjdata-localstack/`` (``KJBOX_LOCALSTACK_DIR``), the repo's own
``config.json`` is never read or written, and ``make_hermetic`` cuts every
outbound side effect a real night has: downloads (the queue never starts a
worker), Divebar download URLs, Telnyx SMS, Web Push and Expo push (captured
in ``app.localstack_pushes``, never sent). No player, overlay or sheet sync is
started because ``create_app`` is given an explicit config.
"""

import gzip
import json
import os
import re
import time
import uuid

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURE_PATH = os.path.join(APP_DIR, "tests", "fixtures", "localstack", "night.json")
EVENT_CODE = "2121"
LOCAL_SECRET = "local-stack-secret"
GEN_URL = "http://127.0.0.1:8010"
GEN_ADMIN_TOKEN = "local-stack-admin"
DECIDE_URL = "http://127.0.0.1:8030"
DIR_ENV = "KJBOX_LOCALSTACK_DIR"

DEFAULT_FLAGS = {
    "make": True, "simple_mode": False, "ask_photo_consent": False,
    "open": True, "auto_approve": True, "auto_reorder": False,
}

# Every table that holds night or singer state. reset_night empties each one
# that exists on each store's connection (an in-memory test DB gives every
# store its own database). rotation_meta keeps the app identity secret and the
# rotation rev; sqlite_sequence is never reset (entry ids stay monotonic, A.15).
_STATE_TABLES = (
    "rotation_entries", "rotation_archive", "rotation_history", "gen_quick_versions",
    "sing_requests", "sing_push_subscriptions", "singer_aliases", "singer_photo_consent",
    "sing_gen_accounts", "song_media_consent", "photo_consent_events", "singer_accounts",
    "app_idempotency", "app_friend_requests", "history_outbox", "sms_log", "sms_opt_outs",
)


def data_dir():
    """The local stack's data dir (never ~/kjdata)."""
    return os.path.expanduser(os.environ.get(DIR_ENV) or "~/kjdata-localstack")


# --- config -----------------------------------------------------------------

def _paths(d):
    videos = os.path.join(d, "videos")
    return {
        "rotation_db_path": os.path.join(d, "rotation.db"),
        "media_db_path": os.path.join(d, "media_library.db"),
        "media_index_path": os.path.join(d, "media_index.json"),
        "external_catalog_db": os.path.join(d, "external_media.db"),
        "catalog_mirror_db": os.path.join(d, "catalog_mirror.db"),
        "song_id_db": os.path.join(d, "song_id.db"),
        "search_log_db": os.path.join(d, "search_log.db"),
        "overlays_path": os.path.join(d, "overlays.json"),
        "download_folder": videos,
        "media_folders": [videos],
        "preview_cache_dir": os.path.join(d, "preview-cache"),
        "filler_music_dir": d,
        "log_file": os.path.join(d, "kj-controller.log"),
        "youtube_cookies_file": os.path.join(d, "youtube_cookies.txt"),
        "flask_secret_key_path": os.path.join(d, "flask_secret"),
        "action_log_dir": os.path.join(d, "action-logs"),
        "external_file_list": "",
        "external_media_mount": "",
    }


def _ensure_vapid(cfg, config_path):
    """Give cfg a VAPID pair persisted in the stack's own config.json, so
    create_app never writes keys into the repo's config.json."""
    if cfg.get("vapid_public_key") and cfg.get("vapid_private_key"):
        return
    from app import _bootstrap_vapid_keys
    _bootstrap_vapid_keys(cfg, config_path)


MARKER = ".kjbox-localstack"

# KJ_CONFIG_OVERRIDES may only point the stack at other gen/decide stacks and
# tune singer rate limits; everything else stays hermetic.
OVERRIDE_KEYS = frozenset({
    "gen_api_url", "gen_api_token", "gen_kjbox_secret",
    "decide_api_url", "decide_kjbox_secret",
    "sing_rate_limit_per_device", "sing_rate_limit_per_ip", "sing_rate_limit_window_s",
})

# Re-asserted after overrides, whatever the stack's own config.json says.
_HERMETIC = {
    "enable_vlc": False,
    "websockify_enabled": False,
    "night_capture_enabled": False,
    "master_sync_enabled": False,
    "vocals_sync_enabled": False,
    "divebar_api_url": "",
    "catalog_mirror_enabled": True,
    "catalog_mirror_max_age_days": 3650,
    "sing_public_url_base": "http://127.0.0.1:8040/sing",
    "sing_short_url_base": "",
    "sing_local_short_host": "",
    "sing_local_url_base": "",
    "history_outbox_interval_s": 5,
    "gen_poll_interval": 5,
    "app_android_sha256_fingerprints": [],
}


def claim_data_dir(d):
    """Refuse any data dir that isn't clearly the local stack's, then mark it.

    The name must contain "localstack"; ``$HOME``, ``/`` and ``~/kjdata`` are
    never accepted; an existing non-empty dir must already carry the
    ``.kjbox-localstack`` marker (written here on first use). Raises ValueError.
    """
    if not d:
        raise ValueError("the local-stack data dir is empty")
    real = os.path.realpath(os.path.expanduser(d))
    home = os.path.realpath(os.path.expanduser("~"))
    kjdata = os.path.realpath(os.path.expanduser("~/kjdata"))
    if (real in (home, "/", kjdata) or real.startswith(kjdata + os.sep)
            or "localstack" not in os.path.basename(real)):
        raise ValueError(f"{d} is not a local-stack dir (its name must contain 'localstack')")
    marker = os.path.join(real, MARKER)
    if os.path.isdir(real) and os.listdir(real) and not os.path.exists(marker):
        raise ValueError(f"{d} is not empty and has no {MARKER} marker")
    os.makedirs(real, exist_ok=True)
    with open(marker, "a", encoding="utf-8"):
        pass
    return real


def hermetic_config(d, overrides=None, *, gen=True, decide=True, action_log=True):
    """Config for a local-stack process rooted at ``d``.

    Starts from the defaults plus ``<d>/config.json`` (never the repo's
    config.json), points every path into ``d``, turns off everything that
    reaches real hardware or real services, and wires gen (8010) and decide
    (8030) local stacks with the shared local partner secret. ``overrides``
    (e.g. ``KJ_CONFIG_OVERRIDES``) may only touch ``OVERRIDE_KEYS``; the
    paths and hermetic switches are re-asserted after them.
    """
    from config import load_config
    overrides = dict(overrides or {})
    bad = sorted(set(overrides) - OVERRIDE_KEYS)
    if bad:
        raise ValueError(f"config overrides not allowed for the local stack: {bad}")
    os.makedirs(d, exist_ok=True)
    config_path = os.path.join(d, "config.json")
    cfg = load_config(config_file=config_path)
    cfg.pop("rotation_sheet_id", None)
    cfg["action_log_enabled"] = bool(action_log)
    if gen:
        cfg.update({"gen_api_url": GEN_URL, "gen_api_token": GEN_ADMIN_TOKEN,
                    "gen_kjbox_secret": LOCAL_SECRET})
    else:
        cfg.update({"gen_api_url": "", "gen_api_token": "", "gen_kjbox_secret": ""})
    if decide:
        cfg.update({"decide_api_url": DECIDE_URL, "decide_kjbox_secret": LOCAL_SECRET})
    else:
        cfg.update({"decide_api_url": "", "decide_kjbox_secret": ""})
    cfg.update(overrides)
    cfg.update(_paths(d))
    cfg.update(_HERMETIC)
    os.makedirs(cfg["download_folder"], exist_ok=True)
    _ensure_vapid(cfg, config_path)
    return cfg


def replay_config(d):
    """Hermetic config for tests and the fixture builder: no gen, no decide
    (the outbox stays inert), no action log. Rate limits stay at their real
    defaults; the replay clears the windows between actions (night time)."""
    return hermetic_config(d, gen=False, decide=False, action_log=False)


def config_overrides_from_env(environ=None, allowed=None):
    """``KJ_CONFIG_OVERRIDES`` as a dict ({} when unset). Bad JSON, a non-object
    or a key outside ``allowed`` (default ``OVERRIDE_KEYS``) is a ValueError."""
    raw = (environ if environ is not None else os.environ).get("KJ_CONFIG_OVERRIDES")
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("KJ_CONFIG_OVERRIDES must be a JSON object")
    bad = sorted(set(data) - (OVERRIDE_KEYS if allowed is None else set(allowed)))
    if bad:
        raise ValueError(f"KJ_CONFIG_OVERRIDES keys not allowed: {bad}")
    return data


# --- hermetic patches ---------------------------------------------------------

def make_hermetic(app, setattr_fn=setattr):
    """Cut every outbound side effect of a real night (A12-R1).

    ``setattr_fn`` is ``monkeypatch.setattr`` in tests so module patches undo.
    """
    import divebar

    # Downloads: claim the worker slot forever, so queued items stay queued
    # (the rotation entry still shows "download queued") and no thread ever
    # reaches YouTube or GCS.
    app.download_queue["worker_running"] = True

    # Divebar download URL lookups hit the Cloud Function; a fake URL lets
    # divebar picks auto-approve exactly as on the box. ".zip" = cdg+mp3, so no
    # sibling-audio lookup either.
    def _fake_divebar_url(file_id, config=None):  # noqa: ARG001
        return f"https://localstack.invalid/divebar/{file_id}.zip" if file_id else None
    setattr_fn(divebar, "get_download_url", _fake_divebar_url)

    # SMS: Telnyx credentials come from the environment (.envrc); never here.
    app.sms_config = {"api_key": None, "from_number": None, "public_key": None}

    # Push: captured, never sent.
    pushes = []
    app.localstack_pushes = pushes
    disp = getattr(app.rotation, "push_dispatcher", None)
    if disp is not None:
        def _capture_expo(sub, message):
            pushes.append({"kind": "expo", "sub": sub.get("id"), "message": message})

        def _capture_web(sub, payload, proposed_state=None):  # noqa: ARG001
            pushes.append({"kind": "web", "sub": sub.get("id"), "payload": payload})
        setattr_fn(disp, "_send_expo", _capture_expo)
        setattr_fn(disp, "_send", _capture_web)
    return app


# --- fixture -------------------------------------------------------------------

def load_fixture(path=None):
    with open(path or FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# PII patterns the committed fixture must never contain (A12-R3).
_PII_PATTERNS = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"\+\d[\d\s().-]{8,}\d")),
    ("phone", re.compile(r"\(\d{3}\)\s*\d{3}[\s.-]?\d{4}")),
    ("phone", re.compile(r"(?<![\w-])\d{3}[\s.]\d{3}[\s.-]\d{4}(?![\w-])")),
    ("phone", re.compile(r"(?<![\w.-])1?\d{10}(?![\w.-])")),
    ("phone", re.compile(r"(?<![\w.:/-])\d{3}[\s.-]\d{4}(?![\w.:/-])")),
    ("token", re.compile(
        r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        r"(?![0-9a-f])")),
    ("token", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("token", re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")),
    ("token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}")),
    ("token", re.compile(r"redacted-[0-9a-f]+")),
    ("token", re.compile(
        r'(?i)\\?"[a-z_]*(?:token|secret|password|authorization|p256dh|endpoint|session_id)'
        r'\\?"\s*:\s*\\?"[^"\\]+')),
)


def pii_hits(text):
    """[(kind, match)] for every phone/email/token-looking string in ``text``."""
    hits = []
    for kind, rx in _PII_PATTERNS:
        hits.extend((kind, m.group(0)) for m in rx.finditer(text))
    return hits


# --- catalog -------------------------------------------------------------------

def build_catalogs(d, fixture):
    """Build the stack's external catalog (local library files) and catalog
    mirror (KN community + KN full + Divebar) from the fixture's ``catalog``."""
    import catalog_mirror
    from catalog import ExternalCatalog

    cat = fixture.get("catalog") or {}
    paths = _paths(d)
    os.makedirs(d, exist_ok=True)

    file_list = os.path.join(d, "library_files.txt")
    with open(file_list, "w", encoding="utf-8") as f:
        for p in cat.get("library") or []:
            f.write(p + "\n")
    ext = ExternalCatalog({}, db_path=paths["external_catalog_db"])
    try:
        ext.build_from_file_list(file_list)
    finally:
        ext.close()

    build = os.path.join(d, "catalog-build")
    os.makedirs(build, exist_ok=True)
    community = os.path.join(build, "community.json.gz")
    full = os.path.join(build, "full.json.gz")
    dvb = os.path.join(build, "divebar.json.gz")
    with gzip.open(community, "wt", encoding="utf-8") as f:
        json.dump({"Items": cat.get("kn_community") or []}, f)
    with gzip.open(full, "wt", encoding="utf-8") as f:
        json.dump(cat.get("kn_full") or [], f)
    with gzip.open(dvb, "wt", encoding="utf-8") as f:
        for row in cat.get("divebar") or []:
            f.write(json.dumps(row) + "\n")
    counts = catalog_mirror.build_mirror_db(paths["catalog_mirror_db"], community, full, dvb)

    # Song identification (song_id.db): which real song a typed query means.
    from scripts.build_song_id_db import build as build_song_id
    tsv = os.path.join(build, "song_id.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        for row in cat.get("song_id") or []:
            f.write("\t".join(str(c) for c in row) + "\n")
    build_song_id([tsv], paths["song_id_db"])
    counts["song_id"] = len(cat.get("song_id") or [])
    return counts


# --- night state -----------------------------------------------------------------

def _clear_rate_limits():
    import sing
    import sing_api_v1
    for clear in (sing._rate_limit_state.clear, sing._validate_rate_limit_state.clear,
                  sing_api_v1.failed_code_limit.clear, sing_api_v1.failed_code_ip_limit.clear,
                  sing_api_v1.gen_verify_limit.clear):
        clear()


def _now_local(conn):
    return conn.execute("SELECT datetime('now', 'localtime')").fetchone()[0]


def _start_new_night(store):
    """A fresh night marker, strictly different from the previous one, so the
    night_id changes (the app must see night_changed after a reset)."""
    from sing_store import NIGHT_STARTED_KEY
    prev = store.get_night_started_at()
    conn = store._get_conn()
    now = _now_local(conn)
    while now == prev:
        time.sleep(0.2)
        now = _now_local(conn)
    store._set_meta(NIGHT_STARTED_KEY, now)
    return now


def get_flags(app):
    store = app.sing_store
    return {
        "make": store.is_accepting_make_requests(),
        "simple_mode": store.is_simple_mode(),
        "ask_photo_consent": bool(store.get_footer_settings().get("ask_photo_consent")),
        "open": store.is_enabled(),
        "auto_approve": store.is_auto_approve(),
        "auto_reorder": store.is_auto_reorder(),
    }


def set_flags(app, flags):
    store = app.sing_store
    setters = {
        "make": store.set_accepting_make_requests,
        "simple_mode": store.set_simple_mode,
        "ask_photo_consent": lambda v: store.set_footer_settings({"ask_photo_consent": v}),
        "open": store.set_enabled,
        "auto_approve": store.set_auto_approve,
        "auto_reorder": store.set_auto_reorder,
    }
    for key, value in flags.items():
        setters[key](bool(value))
    app.rotation.notify_changed()
    return get_flags(app)


def reset_night(app, fixture=None):
    """Empty every night/singer table, start a new night under event code 2121,
    apply the fixture's settings and seed its start rotation.

    Returns ``{entry_ref: entry_id}`` for the seeded entries.
    """
    stores = [app.rotation.store, app.sing_store, getattr(app, "sms_store", None)]
    for store in stores:
        if store is None:
            continue
        conn = store._get_conn()
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in _STATE_TABLES:
            if table in existing:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 — fixed table names
        conn.commit()
    stats = getattr(app, "stats", None)
    if stats is not None:
        conn = stats._get_conn()
        for table in ("play_events", "preview_events"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
        conn.commit()

    store = app.sing_store
    store.set_token(EVENT_CODE)
    _start_new_night(store)
    _clear_rate_limits()
    disp = getattr(app.rotation, "push_dispatcher", None)
    if disp is not None:
        disp._seen_active = set()
        disp._seen_night = None
    pushes = getattr(app, "localstack_pushes", None)
    if pushes is not None:
        pushes.clear()

    flags = dict(DEFAULT_FLAGS)
    flags.update((fixture or {}).get("settings") or {})
    set_flags(app, flags)

    refs = {}
    for e in (fixture or {}).get("start_rotation") or []:
        entry = app.rotation.add_entry(
            e["singer"], e.get("song_artist") or "", file_path=e.get("file_path"),
            duration=e.get("duration"), singers=e.get("singers"))
        if e.get("status") and e["status"] != "Waiting":
            app.rotation.update_status(entry["id"], e["status"])
        refs[e["ref"]] = entry["id"]
    app.rotation.notify_changed()
    return refs


def seed_singers(app, singers):
    """Add singers and their songs to tonight's rotation.

    Each singer is ``{name, email?, device_id?, songs: [{artist, title,
    source_type?}]}``. ``email`` makes them an app user (device id derived the
    way v1 does, linked to that gen account tonight); ``device_id`` a web
    phone. Either way the device owns an approved request per song, linked to
    its entry. With neither, the entries are host-typed.
    """
    from app_identity import app_device_id
    store = app.sing_store
    night = store.get_night_started_at()
    out = []
    for s in singers:
        name = (s.get("name") or "").strip()
        if not name:
            raise ValueError("each singer needs a name")
        email = (s.get("email") or "").strip().lower() or None
        device = (s.get("device_id") or "").strip() or None
        if email and not device:
            device = app_device_id(email, app.app_identity_secret)
        if device:
            store.set_alias(device, name, origin="self")
        if email:
            store.link_account(device, email, "app", night)
        entries = []
        for song in s.get("songs") or []:
            artist = (song.get("artist") or "").strip()
            title = (song.get("title") or "").strip()
            if not title:
                raise ValueError("each song needs a title")
            source_type = song.get("source_type") or "local"
            text = song_text(artist, title)
            entry = app.rotation.add_entry(name, text)
            req_id = None
            if device:
                req = store.create_request(
                    singer_name=name, phone="", song_artist=artist, song_title=title,
                    source_type=source_type, source_ref=f"localstack:{artist} - {title}",
                    source_meta=None, notes="", device_id=device)
                store.mark_approved(req["id"], linked_entry_id=entry["id"])
                req_id = req["id"]
            entries.append({"entry_id": entry["id"], "request_id": req_id,
                            "artist": artist, "title": title})
        out.append({"name": name, "email": email, "device_id": device, "entries": entries})
    outbox = getattr(app, "history_outbox", None)
    if outbox is not None and any(o["email"] for o in out):
        outbox.scan(force=True)   # what the v1 link path does (A8-R6)
    return out


_SINGABLE = ("waiting", "up next", "next")


def _ordered_entries(app):
    conn = app.rotation.store._get_conn()
    rows = conn.execute("SELECT id FROM rotation_entries ORDER BY position, id").fetchall()
    return [app.rotation.store.get_entry(r[0]) for r in rows]


def current_entry(app):
    return next((e for e in _ordered_entries(app)
                 if (e.get("status") or "").lower() in ("now singing", "singing now",
                                                        "singing")), None)


def song_text(artist, title):
    """The box's rotation text for a song: "Title - Artist" (routes._format_song_text)."""
    artist, title = (artist or "").strip(), (title or "").strip()
    return f"{title} - {artist}" if artist and title else (title or artist)


def _play_identity(app, entry):
    """What /play would record as artist/title: the media library's identity
    for the file. Here: the linked request's song, else the entry's file name
    ("DISC - Artist - Title.ext"), else nothing."""
    store = app.sing_store
    night = store.get_night_started_at()
    for r in store.requests_linked_to_entries([entry["id"]], night) if night else []:
        if r.get("song_artist") and r.get("song_title"):
            return r["song_artist"], r["song_title"]
    path = entry.get("file_path") or ""
    if path:
        from catalog import parse_karaoke_filename
        from naming import strip_media_id_token
        _disc, artist, title = parse_karaoke_filename(
            strip_media_id_token(os.path.basename(path)))
        if artist and title:
            return artist, title
    return None, None


def record_play(app, entry_id, artist=None, title=None):
    """A play start for ``entry_id`` now (what /play records on the box),
    with the media library's artist/title (given, or derived)."""
    entry = app.rotation.store.get_entry(entry_id) or {}
    if not (artist and title) and entry:
        artist, title = _play_identity(app, entry)
    return app.stats.record_play(
        f"localstack-{entry_id}", entry_id=entry_id, singer=entry.get("singer"),
        artist=artist or None, title=title or None)


def advance(app):
    """The host's Play/Next: the singer on stage is Done, the next singable
    entry is Now Singing (a play event is recorded, as the box's /play does)
    and the one after it is Up Next — one undoable step."""
    entries = _ordered_entries(app)
    cur = current_entry(app)
    queue = [e for e in entries if (e.get("status") or "").lower() in _SINGABLE
             and (cur is None or e["id"] != cur["id"])]
    updates = []
    if cur is not None:
        updates.append((cur["id"], "Done"))
    nxt = queue[0] if queue else None
    after = queue[1] if len(queue) > 1 else None
    if nxt is not None:
        updates.append((nxt["id"], "Now Singing"))
    if after is not None:
        updates.append((after["id"], "Up Next"))
    if updates:
        app.rotation.update_statuses(updates, label="Advance rotation")
    if nxt is not None:
        record_play(app, nxt["id"])
    get = app.rotation.store.get_entry
    return {
        "done": get(cur["id"]) if cur else None,
        "now_singing": get(nxt["id"]) if nxt else None,
        "up_next": get(after["id"]) if after else None,
    }


def drain_outbox(app, rounds=10):
    """Scan, then deliver everything due now (several rounds of 20)."""
    outbox = app.history_outbox
    sent = 0
    for _ in range(rounds):
        n = outbox.run_once() or 0
        sent += n
        if n == 0:
            break
    return sent


def night_state(app):
    from history_ids import night_id_for_marker, DEFAULT_BOX_ID
    store = app.sing_store
    night = store.get_night_started_at()
    token = store.get_token()
    rotation = []
    for e in _ordered_entries(app):
        rotation.append({
            "id": e["id"], "singer": e.get("singer"), "singers": e.get("singers"),
            "song_artist": e.get("song_artist"), "status": e.get("status"),
            "position": e.get("position"), "file_path": e.get("file_path"),
            "done_at": e.get("done_at"),
        })
    requests = []
    for r in store.night_requests(token, night):
        requests.append({
            "id": r["id"], "singer_name": r.get("singer_name"),
            "song_artist": r.get("song_artist"), "song_title": r.get("song_title"),
            "source_type": r.get("source_type"), "status": r.get("status"),
            "linked_entry_id": r.get("linked_entry_id"), "device_id": r.get("device_id"),
            "for_friend": store.is_friend_request(r["id"]),
            "created_at": r.get("created_at"),
        })
    outbox = []
    rows = store._get_conn().execute(
        "SELECT history_id, op, state, attempts, last_error, payload_json "
        "FROM history_outbox WHERE night_started_at = ? ORDER BY created_at, history_id",
        (night,)).fetchall() if night else []
    for r in rows:
        payload = json.loads(r["payload_json"] or "{}")
        outbox.append({
            "history_id": r["history_id"], "op": r["op"], "state": r["state"],
            "attempts": r["attempts"], "last_error": r["last_error"],
            "gen_email": payload.get("gen_email"), "entry_id": payload.get("entry_id"),
            "artist": payload.get("artist"), "title": payload.get("title"),
            "singers": payload.get("singers"), "sung_at": payload.get("sung_at"),
            "record_state": payload.get("state"),
        })
    box_id = app.kj_config.get("box_id") or DEFAULT_BOX_ID
    return {
        "event_code": token,
        "night_started_at": night,
        "night_id": night_id_for_marker(box_id, night) if night else None,
        "rev": app.rotation.store.get_rev(),
        "flags": get_flags(app),
        "rotation": rotation,
        "requests": requests,
        "outbox": outbox,
        "pushes": len(getattr(app, "localstack_pushes", []) or []),
    }


# --- replay ----------------------------------------------------------------------

class StubIdentities:
    """Gen identities for replayed singer devices: ``token_for(ref, name)``
    registers ``<ref>@replay.invalid`` in a gen stub's ``users`` map and
    returns ``(token, email)``."""

    def __init__(self, users):
        self.users = users

    def token_for(self, ref, name):
        email = f"{ref}@replay.invalid"
        token = f"replay-session-{ref}"
        self.users[token] = {"email": email, "display_name": name, "tenant_id": None}
        return token, email


_HOST_ID_FIELDS = ("id",)
_UUID_NS = uuid.UUID("6b1c3f0e-2a4d-4e7b-9c51-5a0d2e9f7c11")


def _map_host_body(body, entries):
    """Recorded entry refs -> this replay's entry ids. None if nothing maps."""
    body = json.loads(json.dumps(body))
    if "updates" in body:
        ups = [dict(u, id=entries[u["id"]]) for u in body["updates"] if u.get("id") in entries]
        if not ups:
            return None
        body["updates"] = ups
    for k in _HOST_ID_FIELDS:
        if k in body:
            if body[k] not in entries:
                return None
            body[k] = entries[body[k]]
    return body


def replay_night(app, client, fixture, token_for):
    """Replay the fixture night through v1 (singers) and the KJ routes (host).

    ``token_for(device_ref, name) -> (bearer, email)`` supplies a gen identity
    per singer device. Returns a time-free snapshot: final rotation (position
    order, incl. Done/Left), request counts by status, every submit's result
    and the history outbox rows, attributed back to device refs.
    """
    import sing_api_v1  # noqa: F401 — the v1 blueprint must be registered
    entries = reset_night(app, fixture)
    v1 = "/sing/api/v1"
    t = fixture.get("event_code") or EVENT_CODE
    names = {d["ref"]: d["name"] for d in fixture["devices"]}
    tokens, emails, joined = {}, {}, set()
    requests = {}
    submits = []

    def bearer(dev):
        if dev not in tokens:
            tokens[dev], emails[dev] = token_for(dev, names[dev])
        return {"Authorization": f"Bearer {tokens[dev]}"}

    def join(dev, name):
        if dev in joined:
            return
        resp = client.post(f"{v1}/join", json={"t": t, "name": name}, headers=bearer(dev))
        if resp.status_code != 200:
            raise AssertionError(f"join {dev}: {resp.status_code} {resp.get_data(as_text=True)}")
        joined.add(dev)

    for i, act in enumerate(fixture["actions"]):
        _clear_rate_limits()   # the night's minutes between actions
        op = act["op"]
        if op == "submit":
            dev = act["device"]
            join(dev, act["name"])
            cid = str(uuid.uuid5(_UUID_NS, f"{dev}:{i}"))
            body = dict(act["body"], t=t, client_request_id=cid)
            resp = None
            for _ in range(int(act.get("attempts", 1))):
                resp = client.post(f"{v1}/requests", json=body, headers=bearer(dev))
            data = resp.get_json(silent=True) or {}
            submits.append([dev, resp.status_code, data.get("error")])
            req = data.get("request") or {}
            if resp.status_code == 200 and req.get("id"):
                if act.get("ref"):
                    requests[act["ref"]] = req["id"]
                if act.get("entry") and req.get("linked_entry_id"):
                    entries[act["entry"]] = req["linked_entry_id"]
        elif op in ("cancel", "change"):
            rid = requests.get(act["request"])
            if rid is None:
                continue
            dev = act["device"]
            if op == "cancel":
                client.post(f"{v1}/requests/{rid}/cancel", json={"t": t}, headers=bearer(dev))
            else:
                resp = client.post(f"{v1}/requests/{rid}/change",
                                   json=dict(act["body"], t=t), headers=bearer(dev))
                new = (resp.get_json(silent=True) or {}).get("request") or {}
                if resp.status_code == 200 and new.get("id"):
                    requests[act["request"]] = new["id"]
        elif op == "play":
            if act["entry"] in entries:
                record_play(app, entries[act["entry"]], act.get("artist"), act.get("title"))
        elif op == "host":
            body = _map_host_body(act.get("body") or {}, entries)
            if body is None:
                continue
            if act["path"] == "/rotation/move":
                count = len(_ordered_entries(app))
                body["new_position"] = max(1, min(int(body["new_position"]), count))
            resp = client.post(act["path"], json=body)
            if act.get("entry") and resp.status_code == 200:
                new = ((resp.get_json(silent=True) or {}).get("entry") or {}).get("id")
                if new:
                    entries[act["entry"]] = new
        else:
            raise ValueError(f"unknown replay op {op!r}")

    app.history_outbox.scan(force=True)
    return snapshot(app, {v: k for k, v in emails.items()}, submits)


def snapshot(app, email_to_dev, submits):
    store = app.sing_store
    night = store.get_night_started_at()
    rotation = [[e.get("singer") or "", e.get("song_artist") or "", e.get("status") or ""]
                for e in _ordered_entries(app)]
    counts = {}
    for r in store.night_requests(store.get_token(), night):
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    outbox = []
    for r in store.outbox_rows_for_night(night).values():
        p = r.get("payload") or {}
        outbox.append([email_to_dev.get(p.get("gen_email"), "?"), r.get("op"),
                       p.get("state"), p.get("artist") or "", p.get("title") or "",
                       list(p.get("singers") or [])])
    outbox.sort()
    return {"submits": submits, "rotation": rotation,
            "requests": dict(sorted(counts.items())), "outbox": outbox}
