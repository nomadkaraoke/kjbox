#!/usr/bin/env python3
"""Build the local stack's fixture night (tests/fixtures/localstack/night.json).

Source: a recorded night, redacted by ``scripts/night_fixture.py``. By default
``~/nomad-nights/2026-09-24/``: its ``fixture/`` dir (night_fixture.py output)
is used when present, else night_fixture.py is run on ``capture/`` +
``actions.jsonl`` into a temp dir first.

What goes in (and nothing else):
  devices         one ref per singer device (d01, d02 …) + the name it used.
                  Recorded device ids never leave this script.
  start_rotation  the rotation when the recording started.
  actions         every successful singer submit/cancel/change (identical
                  failed retries collapse into one action with ``attempts``),
                  the host's rotation actions (status, add, edit, move, delete,
                  merge, remove/restore singer, priority) and play starts.
                  Entry/request ids become refs (e1225, r973).
  catalog         rows for every song the night touched (local library files,
                  KN community/full, Divebar) + a few synthetic rows the app's
                  fixture searches need ("hello", "starlite muse").
  search_cases    which song covers each search case the tests check.
  expected        the replay snapshot (localstack.replay_night with stub gen
                  identities), so the night-replay test can compare.

Names: real first names stay (the night-recording record allows publishing
them); a full surname is cut to its initial ("Owen Gallagher" -> "Owen G").
Phones, emails, tokens, notes and device ids are dropped, YouTube share
parameters are stripped, and the output is checked with
``localstack.pii_hits`` before it is written (refuses to write on a hit).

Usage:
  python scripts/build_localstack_fixture.py [--night DIR] [--out PATH] [--check]
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)

import localstack  # noqa: E402

DEFAULT_NIGHT = os.path.expanduser("~/nomad-nights/2026-09-24")

SUBMIT_FIELDS = ("song_artist", "song_title", "source_type", "source_ref", "source_meta",
                 "for_friend", "friend_name")
HOST_PATHS = {
    "/rotation/status", "/rotation/add", "/rotation/edit", "/rotation/move",
    "/rotation/delete", "/rotation/singer/merge", "/rotation/singer/remove",
    "/rotation/singer/restore", "/rotation/set-priority",
}
NAME_FIELDS = ("singer", "name", "source_name", "target_name")
# kj_pick version keys worth keeping (ranking + picking + display).
VERSION_KEEP = {"source", "priority_brand", "priority_class", "priority_display",
                "priority_rank", "priority_stated"}
LOCAL_KEEP = {"artist", "title", "disc_id", "filename", "folder", "format", "path"}
KN_KEEP = {"brand_code", "brand_name", "is_community", "song_artist", "song_title",
           "youtube_url", "divebar", "in_library"}
DIVEBAR_KEEP = {"brand", "brand_code", "drive_path", "file_id", "file_size", "format",
                "in_gcs", "quality", "subfolder", "artist", "title", "filename"}

# Synthetic catalog rows for the app's fixture searches (nomad-mobile CLAUDE.md).
EXTRA_LIBRARY = [
    "/media/nomad/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/"
    "Karaoke Version (KVD)/KVD-41000/KVD-41234 - Adele - Hello.mp4",
]
EXTRA_COMMUNITY = [
    {"Id": 900001, "Artist": "Adele", "Title": "Hello", "Brand": "SBI",
     "Watch": "https://www.youtube.com/watch?v=AAAAAAAAAA1"},
    {"Id": 900002, "Artist": "Muse", "Title": "Starlight", "Brand": "SBI",
     "Watch": "https://www.youtube.com/watch?v=AAAAAAAAAA2"},
]
EXTRA_FULL = [
    {"Id": 900101, "Artist": "Muse", "Title": "Starlight", "Brands": "SF,KV,ZM"},
]
EXTRA_DIVEBAR = [
    {"file_id": "localstack-divebar-muse-starlight", "brand": "Sunfly", "brand_code": "SF",
     "artist": "Muse", "title": "Starlight", "filename": "SF250-05 - Muse - Starlight.zip",
     "format": "zip", "file_size": 3456789, "drive_path": "Sunfly/SF250-05 - Muse - Starlight.zip",
     "in_gcs": True, "quality": "", "subfolder": ""},
]
# Song-identification rows (song_id.db: "which real song does the singer
# mean", over songs with AND without karaoke versions). Every catalog song is
# added with karaoke=1; these add popularity and the no-karaoke song.
EXTRA_SONG_ID = [
    ["Adele", "Hello", "90", "1"],
    ["Muse", "Starlight", "70", "1"],
    ["The Strokes", "Machu Picchu", "60", "0"],
]
SEARCH_CASES = {
    "local_video": {"q": "hello", "artist": "Adele", "title": "Hello"},
    # Typo: karaoke search alone finds nothing; /search/identify names the
    # song, then searching its name finds Community + Commercial versions.
    "fuzzy": {"q": "starlite muse", "identify": True, "artist": "Muse",
              "title": "Starlight"},
    # Identified, but no karaoke version anywhere (the make-it card).
    "no_karaoke": {"q": "machu picchu strokes", "identify": True,
                   "artist": "The Strokes", "title": "Machu Picchu"},
    "kn_only": {"q": "join us for a bite", "artist": "Jt Music Ft. Andrea Storm Kaden",
                "title": "Join Us For A Bite"},
    "divebar": {"q": "muse starlight", "artist": "Muse", "title": "Starlight"},
    "community": {"q": "adele hello", "artist": "Adele", "title": "Hello"},
    "many_versions": {"q": "i want it that way", "artist": "Backstreet Boys",
                      "title": "I Want It That Way"},
}


def short_name(name):
    """Keep first names; cut a full surname to an initial."""
    if not isinstance(name, str):
        return name
    out = []
    for part in name.split(" & "):
        words = part.split()
        if len(words) >= 2 and len(words[-1].rstrip(".!")) > 2:
            words = words[:-1] + [words[-1][0]]
        out.append(" ".join(words))
    return " & ".join(out)


def clean_url(url):
    """YouTube URLs keep only the video id (drop share/tracking params)."""
    if not isinstance(url, str) or "youtu" not in url:
        return url
    u = urlparse(url)
    if u.netloc.endswith("youtu.be"):
        vid = u.path.strip("/")
    else:
        vid = (parse_qs(u.query).get("v") or [""])[0]
    return f"https://www.youtube.com/watch?v={vid}" if vid else url


def _pick(d, keys):
    return {k: d[k] for k in sorted(keys) if k in d}


def clean_version(v):
    out = _pick(v, VERSION_KEEP)
    if isinstance(v.get("local"), dict):
        out["local"] = _pick(v["local"], LOCAL_KEEP)
    if isinstance(v.get("kn"), dict):
        kn = _pick(v["kn"], KN_KEEP)
        if kn.get("youtube_url"):
            kn["youtube_url"] = clean_url(kn["youtube_url"])
        if isinstance(kn.get("divebar"), dict):
            kn["divebar"] = _pick(kn["divebar"], DIVEBAR_KEEP)
        out["kn"] = kn
    if isinstance(v.get("divebar"), dict):
        out["divebar"] = _pick(v["divebar"], DIVEBAR_KEEP)
    return out


def clean_meta(meta, source_type):
    if not isinstance(meta, dict):
        return None
    if source_type == "kj_pick":
        out = {k: meta[k] for k in ("group_key", "version_count") if k in meta}
        out["versions"] = [clean_version(v) for v in meta.get("versions") or []]
        return out
    return {k: meta[k] for k in ("brand_code", "disc_id", "format") if k in meta} or None


def clean_submit_body(b):
    body = {k: b[k] for k in SUBMIT_FIELDS if k in b and b[k] not in (None, "")}
    if "source_ref" in body:
        body["source_ref"] = clean_url(body["source_ref"])
    if "source_meta" in b:
        meta = clean_meta(b.get("source_meta"), b.get("source_type"))
        if meta:
            body["source_meta"] = meta
        else:
            body.pop("source_meta", None)
    if "friend_name" in body:
        body["friend_name"] = short_name(body["friend_name"])
    partners = [short_name(p.get("name")) for p in b.get("additional_singers") or []
                if isinstance(p, dict) and p.get("name")]
    if partners:
        body["additional_singers"] = [{"name": n} for n in partners]
    return body


def ensure_redacted(night):
    """The night_fixture.py output dir for ``night`` (runs it if needed)."""
    fixture_dir = os.path.join(night, "fixture")
    if os.path.isfile(os.path.join(fixture_dir, "actions.jsonl")):
        return fixture_dir
    out = tempfile.mkdtemp(prefix="night-fixture-")
    cmd = [sys.executable, os.path.join(HERE, "night_fixture.py"),
           "--capture", os.path.join(night, "capture"), "--out", out]
    actions = os.path.join(night, "actions.jsonl")
    if os.path.isfile(actions):
        cmd += ["--actions", actions]
    subprocess.run(cmd, check=True)
    return out


def start_rotation(redacted):
    path = os.path.join(redacted, "snapshots", "start-rotation.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM rotation_entries ORDER BY position, id").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        singers = json.loads(r["singers_json"]) if r["singers_json"] else None
        out.append({
            "ref": f"e{r['id']}",
            "singer": short_name(r["singer"]),
            "singers": [short_name(s) for s in singers] if singers else None,
            "song_artist": r["song_artist"] or "",
            "status": r["status"],
            "file_path": r["file_path"],
        })
    return out


def _host_body(path, body, known):
    """Recorded KJ body -> fixture body with entry refs; None to skip."""
    b = json.loads(json.dumps(body or {}))
    for k in NAME_FIELDS:
        if k in b:
            b[k] = short_name(b[k])
    if "singers" in b and isinstance(b["singers"], list):
        b["singers"] = [short_name(s) for s in b["singers"]]
    if path == "/rotation/status":
        if "updates" in b:
            ups = [{"id": f"e{u['id']}", "status": u["status"]}
                   for u in b["updates"] if f"e{u.get('id')}" in known]
            if not ups:
                return None
            return {"updates": ups}
        ref = f"e{b.get('id')}"
        return {"id": ref, "status": b["status"]} if ref in known else None
    if "id" in b:
        ref = f"e{b['id']}"
        if ref not in known:
            return None
        b["id"] = ref
    keep = {"/rotation/add": ("singers", "song_artist", "singer"),
            "/rotation/edit": ("id", "singers", "song_artist", "singer"),
            "/rotation/move": ("id", "new_position"),
            "/rotation/delete": ("id",),
            "/rotation/set-priority": ("id", "bias"),
            "/rotation/singer/merge": ("source_name", "target_name"),
            "/rotation/singer/remove": ("name",),
            "/rotation/singer/restore": ("name",)}[path]
    return {k: b[k] for k in keep if k in b}


def build(night):
    redacted = ensure_redacted(night)
    start = start_rotation(redacted)
    known = {e["ref"] for e in start}
    devices, dev_names = {}, {}
    actions = []
    catalog = {"library": set(), "kn_community": {}, "kn_full": {}, "divebar": {}}

    def dev_ref(device_id, name):
        if device_id not in devices:
            devices[device_id] = f"d{len(devices) + 1:02d}"
            dev_names[devices[device_id]] = short_name(name) or devices[device_id]
        return devices[device_id]

    def add_catalog(body):
        st = body.get("source_type")
        ref = body.get("source_ref")
        artist, title = body.get("song_artist") or "", body.get("song_title") or ""
        if st == "local" and ref:
            catalog["library"].add(ref)
        elif st in ("kn", "youtube") and ref and artist and title:
            brand = (body.get("source_meta") or {}).get("brand_code") or "YT"
            catalog["kn_community"][(artist, title, brand)] = {
                "Artist": artist, "Title": title, "Brand": brand, "Watch": ref}
        for v in (body.get("source_meta") or {}).get("versions") or []:
            if v.get("source") == "local" and (v.get("local") or {}).get("path"):
                catalog["library"].add(v["local"]["path"])
            kn = v.get("kn") or {}
            if v.get("source") == "kn" and kn:
                a = kn.get("song_artist") or artist
                t = kn.get("song_title") or title
                code = kn.get("brand_code") or ""
                if kn.get("is_community") and kn.get("youtube_url"):
                    catalog["kn_community"][(a, t, code)] = {
                        "Artist": a, "Title": t, "Brand": code, "Watch": kn["youtube_url"]}
                elif code:
                    row = catalog["kn_full"].setdefault((a, t), {"Artist": a, "Title": t,
                                                                 "Brands": []})
                    if code not in row["Brands"]:
                        row["Brands"].append(code)
                dvb = kn.get("divebar")
                if isinstance(dvb, dict) and dvb.get("file_id"):
                    catalog["divebar"][dvb["file_id"]] = dict(
                        dvb, artist=a, title=t,
                        filename=os.path.basename(dvb.get("drive_path") or "") or None)

    with open(os.path.join(redacted, "actions.jsonl"), encoding="utf-8") as f:
        recorded = [json.loads(line) for line in f if line.strip()]
    recorded.sort(key=lambda a: (a.get("ts") or "", a.get("seq") or 0))

    last_failed = {}   # device -> index of a submit action still collecting retries
    for a in recorded:
        ep = a.get("endpoint") or ""
        body = a.get("body") or {}
        status = a.get("status")
        resp = a.get("response") if isinstance(a.get("response"), dict) else {}
        if ep == "sing.submit" and isinstance(body, dict):
            device_id = (body.get("device_id") or "").strip()
            if not device_id:
                continue
            clean = clean_submit_body(body)
            dev = dev_ref(device_id, body.get("singer_name"))
            prev = last_failed.get(dev)
            if prev is not None and actions[prev]["body"].get("song_title") == clean.get(
                    "song_title") and actions[prev]["body"].get("song_artist") == clean.get(
                    "song_artist"):
                actions[prev]["attempts"] = actions[prev].get("attempts", 1) + 1
                if status == 200:
                    last_failed.pop(dev, None)
                continue
            act = {"op": "submit", "device": dev,
                   "name": short_name(body.get("singer_name")) or dev_names[dev],
                   "body": clean}
            req = resp.get("request") or {}
            if status == 200 and req.get("id"):
                act["ref"] = f"r{req['id']}"
                if req.get("linked_entry_id"):
                    act["entry"] = f"e{req['linked_entry_id']}"
                    known.add(act["entry"])
                last_failed.pop(dev, None)
            elif status in (400, 429):
                # Recorded refusals that v1 now accepts (e.g. kj_pick > 50
                # versions is trimmed, A.24): retried as ONE idempotent request.
                if "too many versions" not in str(resp.get("error") or "") and status != 429:
                    continue
                last_failed[dev] = len(actions)
            else:
                continue
            add_catalog(clean)
            actions.append(act)
        elif ep in ("sing.cancel_request", "sing.change_request") and status == 200:
            req_id = (a.get("view_args") or {}).get("req_id")
            device_id = (body.get("device_id") or "").strip()
            if not req_id or not device_id or device_id not in devices:
                continue
            ref = f"r{req_id}"
            if not any(x.get("ref") == ref for x in actions):
                continue
            act = {"op": "cancel" if ep == "sing.cancel_request" else "change",
                   "device": devices[device_id], "request": ref}
            if act["op"] == "change":
                clean = clean_submit_body(body)
                act["body"] = {k: v for k, v in clean.items()
                               if k in ("source_type", "source_ref", "source_meta",
                                        "song_artist", "song_title")}
                add_catalog(clean)
            actions.append(act)
        elif a.get("actor") == "kj" and a.get("path") in HOST_PATHS and status == 200:
            hb = _host_body(a["path"], body, known)
            if hb is None:
                continue
            act = {"op": "host", "path": a["path"], "body": hb}
            if a["path"] == "/rotation/add":
                new_id = (resp.get("entry") or {}).get("id")
                if new_id:
                    act["entry"] = f"e{new_id}"
                    known.add(act["entry"])
            actions.append(act)
        elif ep == "routes.handle_play" and status == 200:
            ref = f"e{body.get('entry_id')}"
            if ref in known:
                actions.append({"op": "play", "entry": ref})
                if body.get("file_path"):
                    catalog["library"].add(body["file_path"])

    for e in start:
        if e.get("file_path"):
            catalog["library"].add(e["file_path"])
    library = sorted(p for p in catalog["library"] if p) + EXTRA_LIBRARY
    full_rows = [{"Id": 800000 + i, "Artist": r["Artist"], "Title": r["Title"],
                  "Brands": ",".join(r["Brands"])}
                 for i, r in enumerate(sorted(catalog["kn_full"].values(),
                                              key=lambda r: (r["Artist"], r["Title"])))]
    community_rows = [dict(r, Id=700000 + i) for i, r in enumerate(
        sorted(catalog["kn_community"].values(),
               key=lambda r: (r["Artist"], r["Title"], r["Brand"])))]
    fixture = {
        "format": 1,
        "about": ("kjbox local-stack fixture night, built by "
                  "scripts/build_localstack_fixture.py from the redacted 2026-09-24 "
                  "recording (night_fixture.py). Do not edit by hand: rebuild."),
        "source": "~/nomad-nights/2026-09-24 (redacted)",
        "event_code": localstack.EVENT_CODE,
        "settings": dict(localstack.DEFAULT_FLAGS),
        "devices": [{"ref": ref, "name": dev_names[ref]} for ref in
                    sorted(dev_names)],
        "start_rotation": start,
        "actions": actions,
        "catalog": {
            "library": library,
            "kn_community": community_rows + EXTRA_COMMUNITY,
            "kn_full": full_rows + EXTRA_FULL,
            "divebar": sorted(catalog["divebar"].values(), key=lambda r: r["file_id"])
            + EXTRA_DIVEBAR,
            "song_id": song_id_rows(community_rows + EXTRA_COMMUNITY, full_rows + EXTRA_FULL,
                                    catalog["divebar"].values(), actions),
        },
        "search_cases": SEARCH_CASES,
    }
    fixture["expected"] = expected_snapshot(fixture)
    return fixture


def song_id_rows(community, full, divebar, actions):
    """[artist, title, popularity, karaoke] for song_id.db (TSV columns)."""
    rows = {}
    for r in list(community) + list(full):
        rows[(r["Artist"], r["Title"])] = [r["Artist"], r["Title"], "", "1"]
    for r in divebar:
        if r.get("artist") and r.get("title"):
            rows[(r["artist"], r["title"])] = [r["artist"], r["title"], "", "1"]
    for a in actions:
        b = a.get("body") or {}
        if a["op"] == "submit" and b.get("song_artist") and b.get("song_title"):
            rows[(b["song_artist"], b["song_title"])] = [b["song_artist"], b["song_title"], "", "1"]
    for row in EXTRA_SONG_ID:
        rows[(row[0], row[1])] = list(row)
    return sorted(rows.values())


def expected_snapshot(fixture):
    """Replay the fixture once in a throwaway hermetic app (stub gen)."""
    from app import create_app
    from app_identity import GenTokenVerifier
    from gen_client import GenApiError

    class _StubGen:
        def __init__(self):
            self.users = {}

        def get_me(self, session_token):
            user = self.users.get(session_token)
            if user is None:
                raise GenApiError(401, "Invalid session")
            return {"user": dict(user), "has_session": True}

    with tempfile.TemporaryDirectory(prefix="kjbox-replay-") as d:
        app = create_app(config=localstack.replay_config(d))
        app.config["TESTING"] = True
        localstack.make_hermetic(app)
        stub = _StubGen()
        app.gen_verifier = GenTokenVerifier(stub, secret=app.app_identity_secret)
        ids = localstack.StubIdentities(stub.users)
        try:
            with app.test_client() as client:
                return localstack.replay_night(app, client, fixture, ids.token_for)
        finally:
            app.catalog.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--night", default=DEFAULT_NIGHT, help="recorded night dir")
    ap.add_argument("--out", default=localstack.FIXTURE_PATH)
    ap.add_argument("--check", action="store_true",
                    help="build, then fail if it differs from --out (no write)")
    args = ap.parse_args()
    if not os.path.isdir(args.night):
        sys.exit(f"recorded night not found: {args.night}")
    fixture = build(args.night)
    text = json.dumps(fixture, indent=1, ensure_ascii=False, sort_keys=False) + "\n"
    hits = localstack.pii_hits(text)
    if hits:
        sys.exit(f"refusing to write: PII-looking strings {hits[:10]}")
    if args.check:
        with open(args.out, encoding="utf-8") as f:
            same = f.read() == text
        print("fixture up to date" if same else "fixture differs from a fresh build")
        sys.exit(0 if same else 1)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    n = {k: len(v) for k, v in fixture["catalog"].items()}
    print(f"wrote {args.out}: {len(fixture['devices'])} devices, "
          f"{len(fixture['start_rotation'])} start entries, {len(fixture['actions'])} actions, "
          f"catalog {n}, {len(fixture['expected']['outbox'])} outbox records")


if __name__ == "__main__":
    main()
