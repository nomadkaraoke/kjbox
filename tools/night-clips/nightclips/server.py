"""Localhost review page + JSON API (stdlib ``http.server``).

Routes:
  GET  /                    review page            GET /static/<f>   page assets
  GET  /api/state           everything the page needs
  GET  /media/<clip_id>     the clip (HTTP Range supported, for seeking)
  GET  /thumbs/<clip_id>    jpg thumbnail
  POST /api/reassign        {clip_id, song_idx|null}
  POST /api/caption         {selected:[ids]} -> {caption} (regenerated from the template)
  POST /api/draft           {selected, caption}            -> saves draft.json
  POST /api/publish         {selected, caption, allow_unknown} -> saves + starts publisher
  POST /api/posted          {}                             -> writes posted.json

Only binds 127.0.0.1.
"""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import re
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import select as sel
from .clips import Clip

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 1_000_000


class ReviewState:
    def __init__(
        self,
        night: str,
        songs: list[dict],
        clips: list[Clip],
        matches: dict[str, dict],
        cfg: dict,
        work_dir: Path,
        consent_source: str = "events",
        warning: str | None = None,
        publisher: Callable[[list[str], str, bool], dict] | None = None,
        auto_share: bool = False,
    ):
        self.night = night
        self.songs = songs
        self.cfg = cfg
        self.work_dir = Path(work_dir)
        self.consent_source = consent_source
        self.warning = warning
        self.auto_share = auto_share
        self.publisher = publisher or launch_publisher_subprocess(self.work_dir, night, auto_share)
        self.lock = threading.RLock()
        self.clips = {c.id: c for c in clips}
        self.items: list[dict] = []
        for c in clips:
            m = matches.get(c.id) or {"song_idx": None, "auto_song_idx": None, "flags": [],
                                      "offset_s": None, "candidates": []}
            self.items.append(
                {
                    "id": c.id,
                    "filename": c.filename,
                    "start": c.start.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration": c.duration,
                    "width": c.width,
                    "height": c.height,
                    "local": c.local,
                    "error": c.error,
                    "has_thumb": bool(c.thumb),
                    "song_idx": m["song_idx"],
                    "auto_song_idx": m["auto_song_idx"],
                    "flags": list(m["flags"]),
                    "offset_s": m["offset_s"],
                    "candidates": list(m["candidates"]),
                    "reviewed": False,
                }
            )
            self._restatus(self.items[-1])
        self.by_id = {i["id"]: i for i in self.items}
        self.selected: list[str] = sel.preselect(self.items, songs, self.max_clips)
        self.caption = self.default_caption(self.selected)
        self.draft_loaded = False

    # -- helpers -------------------------------------------------------------
    @property
    def max_clips(self) -> int:
        return int(self.cfg.get("max_clips", 20))

    @property
    def draft_path(self) -> Path:
        return self.work_dir / "draft.json"

    @property
    def posted_path(self) -> Path:
        return self.work_dir / "posted.json"

    def _restatus(self, item: dict) -> None:
        idx = item["song_idx"]
        item["status"] = sel.song_status(None if idx is None else self.songs[idx])

    def default_caption(self, selected: list[str]) -> str:
        items = [self.by_id[i] for i in selected if i in self.by_id] if hasattr(self, "by_id") else []
        return sel.render_caption(self.night, items, self.songs, self.cfg.get("caption", {}))

    def posted(self) -> dict | None:
        try:
            return json.loads(self.posted_path.read_text())
        except (OSError, ValueError):
            return None

    # -- draft ---------------------------------------------------------------
    def load_draft(self) -> bool:
        try:
            d = json.loads(self.draft_path.read_text())
        except (OSError, ValueError):
            return False
        with self.lock:
            for cid, a in (d.get("assignments") or {}).items():
                it = self.by_id.get(cid)
                if not it:
                    continue
                idx = a.get("song_idx")
                if idx is None or (isinstance(idx, int) and 0 <= idx < len(self.songs)):
                    it["song_idx"] = idx
                    it["reviewed"] = bool(a.get("reviewed"))
                    self._restatus(it)
            self.selected = [
                cid for cid in d.get("selected") or []
                if cid in self.by_id and sel.is_selectable(self.by_id[cid], allow_unknown=True)
            ][: self.max_clips]
            if d.get("caption"):
                self.caption = d["caption"]
            self.draft_loaded = True
        return True

    def save_draft(self, selected: list[str] | None = None, caption: str | None = None) -> Path:
        with self.lock:
            if selected is not None:
                self.selected = list(selected)
            if caption is not None:
                self.caption = caption
            self.work_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "night": self.night,
                "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
                "selected": self.selected,
                "files": [self.clips[c].path for c in self.selected if c in self.clips],
                "caption": self.caption,
                "assignments": {
                    i["id"]: {"song_idx": i["song_idx"], "reviewed": i["reviewed"]}
                    for i in self.items
                    if i["song_idx"] != i["auto_song_idx"] or i["reviewed"]
                },
            }
            tmp = self.draft_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            tmp.replace(self.draft_path)
            return self.draft_path

    # -- mutations -----------------------------------------------------------
    def reassign(self, clip_id: str, song_idx: int | None) -> dict:
        with self.lock:
            it = self.by_id.get(clip_id)
            if not it:
                raise ValueError("unknown clip")
            if song_idx is not None and song_idx not in it["candidates"]:
                raise ValueError("song is not adjacent to this clip")
            it["song_idx"] = song_idx
            it["reviewed"] = True
            self._restatus(it)
            if clip_id in self.selected and not sel.is_selectable(it, allow_unknown=True):
                self.selected.remove(clip_id)
            return it

    def validate_selection(self, selected: list[str], allow_unknown: bool) -> list[str]:
        errors = []
        if not selected:
            errors.append("Nothing selected.")
        if len(selected) > self.max_clips:
            errors.append(f"Too many clips: {len(selected)} > {self.max_clips}.")
        if len(set(selected)) != len(selected):
            errors.append("Duplicate clips in selection.")
        for cid in selected:
            it = self.by_id.get(cid)
            if not it:
                errors.append(f"Unknown clip {cid}.")
            elif not it["local"]:
                errors.append(f"{it['filename']}: not downloaded.")
            elif it["status"] == sel.EXCLUDED:
                errors.append(f"{it['filename']}: a performer said NO to posting this song.")
            elif it["status"] == sel.UNASSIGNED:
                errors.append(f"{it['filename']}: not matched to a song.")
            elif it["status"] == sel.UNKNOWN and not allow_unknown:
                errors.append(f"{it['filename']}: consent unknown (confirm to include).")
        return errors

    def publish(self, selected: list[str], caption: str, allow_unknown: bool, force: bool = False) -> dict:
        errors = self.validate_selection(selected, allow_unknown)
        if self.posted() and not force:
            errors.append("This night is already marked as posted (posted.json). Confirm to post again.")
        if errors:
            return {"ok": False, "errors": errors}
        self.save_draft(selected, caption)
        files = [self.clips[c].path for c in selected]
        result = self.publisher(files, caption, self.auto_share) or {}
        return {"ok": True, **result}

    def mark_posted(self) -> dict:
        with self.lock:
            payload = {
                "night": self.night,
                "posted_at": dt.datetime.now().isoformat(timespec="seconds"),
                "selected": self.selected,
                "files": [self.clips[c].filename for c in self.selected if c in self.clips],
                "caption": self.caption,
            }
            self.work_dir.mkdir(parents=True, exist_ok=True)
            self.posted_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            return payload

    # -- views ---------------------------------------------------------------
    def to_json(self) -> dict:
        with self.lock:
            return {
                "night": self.night,
                "consent_source": self.consent_source,
                "warning": self.warning,
                "max_clips": self.max_clips,
                "auto_share": self.auto_share,
                "songs": self.songs,
                "clips": self.items,
                "selected": self.selected,
                "caption": self.caption,
                "uncovered": sel.uncovered_performers(self.items, self.songs, self.selected),
                "consenting": list(sel.consenting_performers(self.items, self.songs).values()),
                "posted": self.posted(),
                "draft_loaded": self.draft_loaded,
            }


def launch_publisher_subprocess(work_dir: Path, night: str, auto_share: bool):
    """Run the Playwright publisher in its own process so the browser outlives the request."""

    def run(files: list[str], caption: str, auto_share_flag: bool) -> dict:
        req = Path(work_dir) / "publish_request.json"
        req.write_text(json.dumps({"night": night, "files": files, "caption": caption}, ensure_ascii=False))
        cmd = [sys.executable, "-m", "nightclips.publish", "--request", str(req), "--work-dir", str(work_dir)]
        if auto_share_flag:
            cmd.append("--auto-share")
        proc = subprocess.Popen(cmd)  # inherits the terminal so Andrew sees its log lines
        return {"started": True, "pid": proc.pid,
                "message": "Publisher started — watch the Chromium window and this terminal."}

    return run


# --- HTTP -------------------------------------------------------------------

_SAFE_ID = re.compile(r"^[0-9A-Za-z._-]+$")


def make_handler(state: ReviewState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "night-clips"

        def log_message(self, fmt, *args):  # quiet: only log errors
            if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
                sys.stderr.write("night-clips http: " + (fmt % args) + "\n")

        # -- responses
        def _json(self, obj, status=HTTPStatus.OK):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status, msg):
            self._json({"ok": False, "errors": [msg]}, status)

        def _file(self, path: Path, ctype: str | None = None):
            if not path.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            size = path.stat().st_size
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            status = HTTPStatus.OK
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
                if not m or (not m.group(1) and not m.group(2)):
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:  # suffix range
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _local_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _trusted(self, post: bool) -> str | None:
            """Why this request must be refused, or None. Blocks DNS rebinding (Host
            must be our loopback address) and cross-site POSTs from other pages in
            Andrew's browser (Origin must be ours; JSON content type forces a CORS
            preflight, which this server never grants)."""
            if (self.headers.get("Host") or "") not in self._local_hosts():
                return "unexpected Host"
            if not post:
                return None
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{h}" for h in self._local_hosts()}:
                return "cross-origin request"
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                return "Content-Type must be application/json"
            return None

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        # -- GET
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            why = self._trusted(post=False)
            if why:
                return self._error(HTTPStatus.FORBIDDEN, why)
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if not _SAFE_ID.match(name):
                    return self._error(HTTPStatus.NOT_FOUND, "not found")
                return self._file(STATIC_DIR / name)
            if path == "/api/state":
                return self._json(state.to_json())
            if path.startswith("/media/") or path.startswith("/thumbs/"):
                kind, cid = path.strip("/").split("/", 1)
                cid = cid.removesuffix(".jpg")
                clip = state.clips.get(cid) if _SAFE_ID.match(cid) else None
                if not clip:
                    return self._error(HTTPStatus.NOT_FOUND, "unknown clip")
                if kind == "thumbs":
                    if not clip.thumb:
                        return self._error(HTTPStatus.NOT_FOUND, "no thumbnail")
                    return self._file(Path(clip.thumb), "image/jpeg")
                ctype = "video/quicktime" if clip.filename.lower().endswith(".mov") else "video/mp4"
                return self._file(Path(clip.path), ctype)
            return self._error(HTTPStatus.NOT_FOUND, "not found")

        # -- POST
        def do_POST(self):
            why = self._trusted(post=True)
            if why:
                return self._error(HTTPStatus.FORBIDDEN, why)
            path = self.path.split("?", 1)[0]
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as e:
                return self._error(HTTPStatus.BAD_REQUEST, f"bad request: {e}")
            try:
                if path == "/api/reassign":
                    idx = body.get("song_idx")
                    if idx is not None and not isinstance(idx, int):
                        raise ValueError("song_idx must be an integer or null")
                    state.reassign(str(body.get("clip_id")), idx)
                    return self._json({"ok": True, "state": state.to_json()})
                if path == "/api/caption":
                    return self._json({"ok": True, "caption": state.default_caption(_ids(body))})
                if path == "/api/draft":
                    ids = _ids(body)
                    errs = [e for e in state.validate_selection(ids, allow_unknown=True)
                            if not e.startswith("Nothing selected")]
                    if errs:
                        return self._json({"ok": False, "errors": errs}, HTTPStatus.BAD_REQUEST)
                    p = state.save_draft(ids, str(body.get("caption") or ""))
                    return self._json({"ok": True, "path": str(p)})
                if path == "/api/publish":
                    res = state.publish(_ids(body), str(body.get("caption") or ""),
                                        bool(body.get("allow_unknown")), bool(body.get("force")))
                    return self._json(res, HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST)
                if path == "/api/posted":
                    return self._json({"ok": True, "posted": state.mark_posted()})
            except ValueError as e:
                return self._error(HTTPStatus.BAD_REQUEST, str(e))
            return self._error(HTTPStatus.NOT_FOUND, "not found")

    return Handler


def _ids(body: dict) -> list[str]:
    ids = body.get("selected") or []
    if not isinstance(ids, list):
        raise ValueError("selected must be a list")
    return [str(i) for i in ids]


def make_server(state: ReviewState, port: int = 8765, tries: int = 20) -> ThreadingHTTPServer:
    """Bind 127.0.0.1:port (or the next free port; port 0 = any)."""
    handler = make_handler(state)
    last = None
    for p in ([0] if port == 0 else range(port, port + tries)):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), handler)
            srv.daemon_threads = True
            return srv
        except OSError as e:
            last = e
    raise OSError(f"no free port from {port}: {last}")
