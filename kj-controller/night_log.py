"""Night log: a timestamped record of what happened on any past karaoke night.

Answers "what got sung last Thursday, by whom, who added it (KJ or the
singer's phone), and where the media came from" by joining:

- ``rotation_archive`` (every past night's rotation entries) and the live
  ``rotation_entries`` (tonight), in rotation.db;
- ``sing_requests`` (singer-phone requests, linked to the entry they created);
- ``play_events`` + ``media_library`` (play timestamps and media identity), in
  the media DB.

Archive rows written since this module shipped carry the original entry id,
``done_at`` and the download/gen columns, so they link exactly. Older rows only
have singer / song / file_path / created_at, so they are linked best-effort:
file_path -> media_id -> the play_event for that singer that night (which
carries the original entry id and the time it was sung) -> the sing_request
that created that entry. Requests that still don't link are matched by time
(an approval creates its entry within a second or two).

Nights roll at noon, matching action_recorder / night captures: a show that
runs past midnight is one night. Each entry is placed by when it was sung,
falling back to when it was added.
"""

import bisect
import datetime as dt
import json
import os

from naming import extract_media_id

# Request types that create a rotation entry (vs. reorders / tips).
_ADD_REQUEST_TYPES = ("local", "youtube", "kn", "divebar", "make", "kj_pick")

REQUEST_SOURCE_LABELS = {
    "local": "Library search",
    "youtube": "YouTube",
    "kn": "KaraokeNerds",
    "divebar": "Divebar",
    "make": "Make it (gen)",
    "kj_pick": "KJ picked version",
}

MEDIA_LABELS = {
    "library": "Library",
    "master": "Nomad master",
    "community": "Divebar",
    "youtube": "YouTube",
    "upload": "Upload",
    "gen": "Generated",
    "none": "No media",
}

# How long after an entry was added a request may have been reviewed and
# still count as the request that created it (approval -> add_entry).
_REQUEST_MATCH_SECONDS = 5
_TS_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return dt.datetime.strptime(ts[:19], _TS_FMT)
    except (TypeError, ValueError):
        return None


def night_of(ts):
    """Night (ISO date) a local timestamp belongs to: before noon = previous night."""
    t = _parse_ts(ts) if isinstance(ts, str) else ts
    if t is None:
        return None
    return (t - dt.timedelta(hours=12)).date().isoformat()


def _norm(s):
    return " ".join((s or "").split()).lower()


def _is_done(status):
    return (status or "").strip().lower() == "done"


def _media_kind(file_path, lib_row):
    """Classify where an entry's media came from."""
    if not file_path:
        return "none"
    if lib_row and lib_row.get("source"):
        return lib_row["source"]
    p = file_path.replace("\\", "/")
    if "/downloads/youtube/" in p:
        return "youtube"
    if "/downloads/community/" in p:
        return "community"
    if "/downloads/upload/" in p:
        return "upload"
    if "/NOMAD-" in p:
        return "master"
    mid = extract_media_id(p) or ""
    prefix = mid.split("-", 1)[0]
    return {"yt": "youtube", "db": "community", "up": "upload",
            "nomad": "master", "gen": "gen", "lib": "library"}.get(prefix, "library")


class NightLog:
    """Read-only queries over the rotation DB + media DB."""

    def __init__(self, rotation_store, media_conn_fn=None):
        self.rotation_store = rotation_store
        # Callable returning a connection to the media DB (play_events +
        # media_library), or None when stats are unavailable.
        self.media_conn_fn = media_conn_fn

    # ------------------------------------------------------------------
    # Raw loading
    # ------------------------------------------------------------------

    def _rotation_rows(self):
        conn = self.rotation_store._get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(rotation_archive)")}
        extra = [c for c in ("entry_id", "done_at", "updated_at", "download_source",
                             "gen_job_id", "gen_status", "singers_json", "paid")
                 if c in cols]
        sel = ", ".join(["id", "singer", "song_artist", "status", "notes", "position",
                         "file_path", "created_at"] + extra)
        rows = []
        for r in conn.execute(f"SELECT {sel} FROM rotation_archive"):
            d = dict(r)
            d["archive_id"] = d.pop("id")
            d["live"] = False
            rows.append(d)
        for r in conn.execute(
                "SELECT id AS entry_id, singer, song_artist, status, notes, position, "
                "file_path, created_at, done_at, updated_at, download_source, "
                "gen_job_id, gen_status, singers_json, paid FROM rotation_entries"):
            d = dict(r)
            d["archive_id"] = None
            d["live"] = True
            rows.append(d)
        return rows

    def _requests(self):
        conn = self.rotation_store._get_conn()
        try:
            ph = ",".join("?" * len(_ADD_REQUEST_TYPES))
            return [dict(r) for r in conn.execute(
                "SELECT id, created_at, reviewed_at, singer_name, song_artist, "
                "song_title, source_type, source_meta, linked_entry_id "
                f"FROM sing_requests WHERE status='approved' AND source_type IN ({ph})",
                _ADD_REQUEST_TYPES)]
        except Exception:
            return []  # sing_requests table absent (fresh / test DB)

    def _media_conn(self):
        if not self.media_conn_fn:
            return None
        try:
            return self.media_conn_fn()
        except Exception:
            return None

    def _plays(self, conn):
        """Live play events, played_at converted from UTC to local time."""
        if conn is None:
            return []
        try:
            return [dict(r) for r in conn.execute(
                "SELECT media_id, entry_id, singer_norm, "
                "datetime(played_at, 'localtime') AS played_local "
                "FROM play_events WHERE source='live'")]
        except Exception:
            return []

    @staticmethod
    def _library_rows(conn, paths):
        if conn is None or not paths:
            return {}
        out = {}
        paths = list(paths)
        try:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        "SELECT media_id, source, file_path, "
                        "datetime(created_at, 'localtime') AS created_local "
                        f"FROM media_library WHERE file_path IN ({ph})", chunk):
                    out[r["file_path"]] = dict(r)
        except Exception:
            return {}
        return out

    # ------------------------------------------------------------------
    # Linking
    # ------------------------------------------------------------------

    def _build(self):
        rows = self._rotation_rows()
        requests = self._requests()
        mconn = self._media_conn()
        plays = self._plays(mconn)
        libs = self._library_rows(mconn, {r["file_path"] for r in rows if r.get("file_path")})

        plays_by_entry = {}
        plays_by_media = {}
        for p in plays:
            if p.get("entry_id") is not None:
                plays_by_entry.setdefault(p["entry_id"], p)
            plays_by_media.setdefault(p["media_id"], []).append(p)

        req_by_entry = {}
        for q in requests:
            if q.get("linked_entry_id") is not None:
                req_by_entry.setdefault(q["linked_entry_id"], q)

        claimed_plays = set()
        claimed_reqs = set()

        # Pass 1: exact links (rows that know their entry id).
        for r in rows:
            lib = libs.get(r.get("file_path"))
            r["_lib"] = lib
            r["media_id"] = (lib or {}).get("media_id") or extract_media_id(r.get("file_path") or "")
            eid = r.get("entry_id")
            if eid is not None:
                p = plays_by_entry.get(eid)
                if p:
                    r["_play"] = p
                    claimed_plays.add(id(p))
                q = req_by_entry.get(eid)
                if q:
                    r["_req"] = q
                    claimed_reqs.add(q["id"])

        # Pass 2: legacy rows — media_id + singer -> play_event (-> entry id).
        for r in rows:
            # Only sung rows own a play: an unsung duplicate of the same song
            # (e.g. a re-added entry left Waiting) must not steal its timestamp.
            if r.get("entry_id") is not None or not r.get("media_id") or not _is_done(r.get("status")):
                continue
            created = _parse_ts(r.get("created_at"))
            singer = _norm(r.get("singer"))
            best = None
            for p in plays_by_media.get(r["media_id"], ()):
                if id(p) in claimed_plays:
                    continue
                pt = _parse_ts(p.get("played_local"))
                if pt is None or (created and pt < created - dt.timedelta(minutes=1)):
                    continue
                if created and pt - created > dt.timedelta(hours=18):
                    continue
                if p.get("singer_norm") and singer and p["singer_norm"] != singer:
                    continue
                if best is None or pt < _parse_ts(best["played_local"]):
                    best = p
            if best:
                r["_play"] = best
                claimed_plays.add(id(best))
                if best.get("entry_id") is not None:
                    r["entry_id_inferred"] = best["entry_id"]
                    q = req_by_entry.get(best["entry_id"])
                    if q and q["id"] not in claimed_reqs:
                        r["_req"] = q
                        claimed_reqs.add(q["id"])

        # Pass 3: remaining legacy rows — request reviewed right when the entry
        # was created, for the same singer (or the same song).
        open_reqs = sorted(
            ((t, q) for q in requests if q["id"] not in claimed_reqs
             for t in [_parse_ts(q.get("reviewed_at") or q.get("created_at"))] if t),
            key=lambda tq: tq[0])
        req_times = [t for t, _ in open_reqs]
        window = dt.timedelta(seconds=_REQUEST_MATCH_SECONDS)
        for r in rows:
            if r.get("_req") or r.get("entry_id") is not None:
                continue
            created = _parse_ts(r.get("created_at"))
            if created is None:
                continue
            singer = _norm(r.get("singer"))
            song = _norm(r.get("song_artist"))
            i = bisect.bisect_left(req_times, created - window)
            while i < len(open_reqs) and req_times[i] <= created + window:
                q = open_reqs[i][1]
                i += 1
                if q["id"] in claimed_reqs:
                    continue
                # Same singer, or (the KJ renamed them since) the same song:
                # title AND artist both present, so a short title can't match alone.
                title = _norm(q.get("song_title"))
                artist = _norm(q.get("song_artist"))
                same_song = bool(title and artist and title in song and artist in song)
                if _norm(q.get("singer_name")) == singer or same_song:
                    r["_req"] = q
                    claimed_reqs.add(q["id"])
                    break

        live_night = self._live_night()
        entries = [self._to_entry(r) for r in rows]
        for e in entries:
            # Tonight's not-yet-sung entries (e.g. the starter added when the
            # rotation was reset that morning) belong to the coming night.
            if e["live"] and not e["sung_at"] and live_night:
                e["night"] = live_night
        return entries

    def _live_night(self):
        """Night the live rotation belongs to, from when it was last reset."""
        try:
            row = self.rotation_store._get_conn().execute(
                "SELECT value FROM rotation_meta WHERE key='night_started_at'").fetchone()
        except Exception:
            return None
        t = _parse_ts(row[0]) if row else None
        if t is None:
            return None
        # Reset in the small hours = mid-show reset of that night; any later
        # reset (e.g. the next morning) is prep for the coming night.
        return (t - dt.timedelta(days=1) if t.hour < 6 else t).date().isoformat()
    @staticmethod
    def _to_entry(r):
        play = r.get("_play") or {}
        req = r.get("_req")
        lib = r.get("_lib")
        sung_at = None
        if _is_done(r.get("status")):
            sung_at = r.get("done_at") or play.get("played_local")
        anchor = sung_at or r.get("created_at")
        kind = _media_kind(r.get("file_path"), lib)

        # Downloaded/generated during the night vs already on the box.
        downloaded = bool(r.get("download_source")) or bool(r.get("gen_job_id"))
        lib_created = _parse_ts((lib or {}).get("created_local"))
        created = _parse_ts(r.get("created_at"))
        if not downloaded and lib_created and created and kind != "library":
            downloaded = lib_created >= created - dt.timedelta(minutes=10)

        singers = None
        if r.get("singers_json"):
            try:
                singers = json.loads(r["singers_json"]) or None
            except (TypeError, ValueError):
                singers = None

        added_by = {"by": "kj"}
        if req:
            added_by = {
                "by": "singer",
                "request_id": req["id"],
                "source_type": req.get("source_type"),
                "source_label": REQUEST_SOURCE_LABELS.get(req.get("source_type"),
                                                          req.get("source_type")),
                "requested_at": req.get("created_at"),
                "approved_at": req.get("reviewed_at"),
                "requested_as": " - ".join(x for x in (req.get("song_artist"),
                                                       req.get("song_title")) if x),
                "singer_name": req.get("singer_name"),
            }
        elif r.get("entry_id") is None and not r.get("entry_id_inferred"):
            # Legacy row we could not trace: can't tell KJ from singer reliably.
            added_by = {"by": "kj", "inferred": True}

        file_path = r.get("file_path")
        return {
            "night": night_of(anchor),
            "live": r.get("live", False),
            "entry_id": r.get("entry_id") or r.get("entry_id_inferred"),
            "archive_id": r.get("archive_id"),
            "singer": r.get("singer"),
            "singers": singers,
            "song": r.get("song_artist"),
            "status": r.get("status"),
            "sung": _is_done(r.get("status")),
            "added_at": r.get("created_at"),
            "sung_at": sung_at,
            # When playback started (play_events). sung_at is when the KJ marked the
            # entry Done, i.e. the song's END on nights since the Night Log shipped.
            "started_at": play.get("played_local"),
            "notes": r.get("notes") or "",
            "paid": bool(r.get("paid")),
            "added_by": added_by,
            "media": {
                "kind": kind,
                "label": MEDIA_LABELS.get(kind, kind),
                "file": os.path.basename(file_path) if file_path else None,
                "file_path": file_path,
                "media_id": r.get("media_id"),
                "fetched_tonight": downloaded,
                "download_source": r.get("download_source"),
                "gen_job_id": r.get("gen_job_id"),
            },
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_nights(self, min_songs=10):
        """Summaries of each night, newest first, with at least min_songs sung."""
        nights = {}
        for e in self._build():
            if not e["night"]:
                continue
            n = nights.setdefault(e["night"], {
                "night": e["night"], "entries": 0, "sung": 0, "singers": set(),
                "from_singers": 0, "fetched": 0, "first_at": None, "last_at": None,
                "live": False,
            })
            n["entries"] += 1
            n["live"] = n["live"] or e["live"]
            if not e["sung"]:
                continue
            n["sung"] += 1
            n["singers"].add(_norm(e["singer"]))
            if e["added_by"]["by"] == "singer":
                n["from_singers"] += 1
            if e["media"]["fetched_tonight"]:
                n["fetched"] += 1
            t = e["sung_at"] or e["added_at"]
            if t and (n["first_at"] is None or t < n["first_at"]):
                n["first_at"] = t
            if t and (n["last_at"] is None or t > n["last_at"]):
                n["last_at"] = t
        out = []
        for n in nights.values():
            if n["sung"] < (min_songs or 0):
                continue
            n["singers"] = len(n["singers"])
            out.append(n)
        out.sort(key=lambda n: n["night"], reverse=True)
        return out

    def get_night(self, night):
        """Every entry of one night, in the order things happened."""
        entries = [e for e in self._build() if e["night"] == night]
        entries.sort(key=lambda e: (
            0 if e["sung_at"] else 1,
            e["sung_at"] or e["added_at"] or "",
            e["added_at"] or "",
        ))
        return entries
