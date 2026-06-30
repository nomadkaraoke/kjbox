# Browser Preview Playback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a KJ audition any supported file (native video, exotic video via cached transcode, CDG zips, audio, YouTube, divebar GCS mirror) in their browser, with seek, from every link/play surface, without touching the live primary player.

**Architecture:** A new `PreviewService` (backend) resolves a per-source *descriptor* to the lightest delivery mode and serves bytes over new `/preview/*` routes (HTTP 206 range, CDG parts, HLS). Divebar candidates are downloaded once into a content-addressed cache and then treated exactly like local files. Exotic video transcodes to HLS once into the same cache (`.done`-gated, LRU-evicted). A reusable `preview.js` modal + vendored `cdg.js` canvas renderer mount the right player; CDG and native paths use zero device CPU.

**Tech Stack:** Python/Flask, ffmpeg/ffprobe, vanilla JS (no build), hls.js (vendored), pytest (+ `node` for the CDG renderer test).

## Global Constraints

- **No build step** — frontend is plain JS files loaded by `templates/index.html`; escape user/text with the existing `escHtml`.
- **Reuse trust boundaries** — local paths via `MediaIndex.validate_path` (+ `external_media_mount` fallback as `/play` does); never serve a path outside configured roots.
- **Never serve a truncated transcode** — a transcode cache dir is only valid if it contains a `.done` sentinel (written after ffmpeg exits 0).
- **Live-show safety** — transcode runs `nice -n 19` (+ `ionice -c3` when available), single active job (new preview bumps the old). Preview never uses the device A/V output.
- **kjbox prod rules** — backend changes need a service restart (interrupts playback) → deploy is left for Andrew; bump `pyproject.toml` version in the PR.
- **Tokens are opaque** — raw absolute paths are never round-tripped through the browser; the client only holds a `token`.
- Tests live under `kj-controller/tests/unit/` and `kj-controller/tests/integration/`; app built via `create_app(config=mock_config)`; route tests use `flask_test_client`.

---

## File Structure

**Backend (new):**
- `kj-controller/preview_cache.py` — `PreviewCache`: content-addressed keys, dirs, `.done` gating, LRU eviction, access touch.
- `kj-controller/preview_transcode.py` — `TranscodeManager`: ffmpeg→HLS, single active job, `.done` writer.
- `kj-controller/preview.py` — `PreviewService`: `resolve(descriptor)`, token registry, divebar-blob ensure, CDG extract→cache, serving accessors, `close`, GC. Also pure helper `parse_range(header, size)`.

**Backend (modified):**
- `kj-controller/routes.py` — `/preview/*` endpoints + range `Response` builder.
- `kj-controller/app.py` — attach `flask_app.preview = PreviewService(cfg, media)` in **both** `create_app` and `start_app`.
- `kj-controller/config.py` — preview cache/transcode defaults.

**Frontend (new):**
- `kj-controller/static/cdg.js` — `CDGPlayer` parser/renderer (UMD-safe so `node` can `require` it).
- `kj-controller/static/preview.js` — `openPreview(descriptor)`, `previewButtonHtml(descriptor)`, teardown.
- `kj-controller/static/vendor/hls.min.js` — vendored, lazy-loaded.

**Frontend (modified):**
- `kj-controller/templates/index.html` — preview-modal markup + `<script>` includes.
- `kj-controller/static/style.css` — preview-modal styles.
- `kj-controller/static/app.js` — add a preview button to the 3 rotation row renderers + Available Songs rows.

**Tests (new):**
- `tests/unit/test_preview_cache.py`, `tests/unit/test_preview_range.py`, `tests/unit/test_preview_transcode.py`, `tests/unit/test_preview_service.py`, `tests/integration/test_preview_routes.py`, `tests/unit/test_cdg_renderer.py` (shells `node`).

---

## Interfaces (authoritative — tasks must match these names/types)

```python
# preview.py
def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return (start, end) inclusive byte offsets, or None if no/!satisfiable range."""

class PreviewService:
    def __init__(self, config: dict, media): ...
    def resolve(self, descriptor: dict) -> dict:
        # descriptor: {"source":"local"|"divebar"|"youtube",
        #   "file_path"?, "file_id"?, "format"?, "youtube_url"?, "prefer_transcode"?}
        # returns: {"mode": "native_video"|"native_audio"|"cdg"|"hls"|"youtube"|"unavailable",
        #   "token"?: str, "title": str, "reason"?: str, "youtube_url"?: str}
    def token_info(self, token: str) -> dict | None         # entry or None
    def cdg_part_path(self, token: str, part: str) -> str | None   # part in {"audio","graphics"}
    def hls_path(self, token: str, name: str) -> str | None        # name in {index.m3u8, seg-N.ts}
    def close(self, token: str | None) -> None              # close one or all; kills transcode

# token entry shape (token_info return):
#   {"kind": "native_video"|"native_audio"|"cdg"|"hls",
#    "path"?: str, "mime"?: str,              # native_*
#    "audio"?: str, "graphics"?: str,         # cdg (cache paths)
#    "hls_dir"?: str,                          # hls
#    "created": float}

# preview_cache.py
class PreviewCache:
    def __init__(self, root: str, max_bytes: int): ...
    def local_key(self, real_path: str) -> str        # sha1(realpath+size+mtime+PARAMS_VERSION)
    def gcs_key(self, file_id: str) -> str            # sha1(file_id+PARAMS_VERSION)
    def transcode_dir(self, key: str) -> str          # <root>/transcode/<key>
    def is_done(self, key: str) -> bool               # transcode_dir/.done exists
    def mark_done(self, key: str) -> None
    def cdg_dir(self, key: str) -> str                # <root>/cdg/<key>
    def gcsblob_path(self, file_id: str, name: str) -> str   # <root>/gcsblob/<file_id>/<name>
    def touch(self, path: str) -> None                # bump atime/mtime for LRU
    def evict_if_needed(self) -> None                 # delete oldest complete entries over cap

# preview_transcode.py
class TranscodeBusy(Exception): ...
class TranscodeError(Exception): ...
class TranscodeManager:
    def __init__(self, config: dict): ...
    def ensure_hls(self, source_path: str, dest_dir: str, mark_done) -> str:
        # returns path to dest_dir/index.m3u8 once it exists; bumps any active job.
        # mark_done: zero-arg callable invoked when ffmpeg exits 0.
    def kill_active(self) -> None
```

```js
// cdg.js  (UMD: window.CDGPlayer in browser; module.exports in node)
class CDGPlayer {
  constructor(canvas)          // canvas may be null in node tests
  load(uint8Bytes)             // parse .cdg into packets
  renderAt(timeSeconds)        // apply packets up to floor(time*300); paints canvas if present
  reset()                      // clear state (used on backward seek)
  // test hooks: this.width=300 this.height=216; getPixelRGBA(x,y) -> [r,g,b,a]
}

// preview.js
function openPreview(descriptor)        // descriptor as backend; +{title?, link_context?}
function previewButtonHtml(descriptor)  // returns a one-line ▶︎ button string (escaped)
function closePreview()
```

---

### Task 1: PreviewCache — content-addressed dirs, `.done`, LRU

**Files:**
- Create: `kj-controller/preview_cache.py`
- Test: `kj-controller/tests/unit/test_preview_cache.py`

**Interfaces:** Produces `PreviewCache` (see Interfaces block).

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_preview_cache.py
import os, time, pytest
from preview_cache import PreviewCache

def _cache(tmp_path): return PreviewCache(str(tmp_path), max_bytes=1000)

def test_local_key_changes_with_mtime(tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x"*10)
    c = _cache(tmp_path); k1 = c.local_key(str(f))
    os.utime(f, (time.time()+50, time.time()+50)); k2 = c.local_key(str(f))
    assert k1 != k2 and len(k1) == 40

def test_gcs_key_stable(tmp_path):
    c = _cache(tmp_path)
    assert c.gcs_key("abc") == c.gcs_key("abc") and len(c.gcs_key("abc")) == 40

def test_done_gating(tmp_path):
    c = _cache(tmp_path); d = c.transcode_dir("k"); os.makedirs(d)
    assert c.is_done("k") is False
    c.mark_done("k"); assert c.is_done("k") is True

def test_evict_drops_oldest_complete_over_cap(tmp_path):
    c = PreviewCache(str(tmp_path), max_bytes=50)
    for name in ("k1", "k2"):
        d = c.transcode_dir(name); os.makedirs(d)
        with open(os.path.join(d, "seg-0.ts"), "wb") as fh: fh.write(b"y"*40)
        c.mark_done(name); time.sleep(0.01)
    c.evict_if_needed()  # 80 bytes of complete entries > 50 cap → oldest (k1) removed
    assert not os.path.isdir(c.transcode_dir("k1"))
    assert os.path.isdir(c.transcode_dir("k2"))

def test_evict_keeps_incomplete(tmp_path):
    c = PreviewCache(str(tmp_path), max_bytes=10)
    d = c.transcode_dir("k"); os.makedirs(d)
    with open(os.path.join(d, "seg-0.ts"), "wb") as fh: fh.write(b"z"*40)
    c.evict_if_needed()  # not .done → never evicted
    assert os.path.isdir(d)
```

- [ ] **Step 2: Run — expect FAIL** (`pytest tests/unit/test_preview_cache.py -v`) — "No module named preview_cache".

- [ ] **Step 3: Implement**

```python
# preview_cache.py
"""Content-addressed on-disk cache for preview transcodes, GCS blobs, CDG extracts."""
import hashlib, os, shutil

PARAMS_VERSION = "1"   # bump to invalidate all transcodes when ffmpeg settings change

class PreviewCache:
    def __init__(self, root, max_bytes):
        self.root = root
        self.max_bytes = int(max_bytes)
        for sub in ("transcode", "gcsblob", "cdg"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)

    def local_key(self, real_path):
        st = os.stat(real_path)
        raw = f"{os.path.realpath(real_path)}|{st.st_size}|{int(st.st_mtime)}|{PARAMS_VERSION}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def gcs_key(self, file_id):
        return hashlib.sha1(f"{file_id}|{PARAMS_VERSION}".encode("utf-8")).hexdigest()

    def transcode_dir(self, key): return os.path.join(self.root, "transcode", key)
    def cdg_dir(self, key):       return os.path.join(self.root, "cdg", key)
    def gcsblob_path(self, file_id, name):
        d = os.path.join(self.root, "gcsblob", file_id); os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    def _done_marker(self, key): return os.path.join(self.transcode_dir(key), ".done")
    def is_done(self, key):  return os.path.exists(self._done_marker(key))
    def mark_done(self, key):
        open(self._done_marker(key), "w").close()

    def touch(self, path):
        try: os.utime(path, None)
        except OSError: pass

    def _dir_size(self, d):
        total = 0
        for root, _dirs, files in os.walk(d):
            for f in files:
                try: total += os.path.getsize(os.path.join(root, f))
                except OSError: pass
        return total

    def evict_if_needed(self):
        # Only complete transcode entries are eligible; oldest-access-first.
        tdir = os.path.join(self.root, "transcode")
        entries = []
        for name in os.listdir(tdir):
            d = os.path.join(tdir, name)
            if os.path.isdir(d) and os.path.exists(os.path.join(d, ".done")):
                entries.append((os.path.getmtime(os.path.join(d, ".done")), d))
        total = sum(self._dir_size(d) for _t, d in entries)
        for _t, d in sorted(entries):
            if total <= self.max_bytes: break
            sz = self._dir_size(d); shutil.rmtree(d, ignore_errors=True); total -= sz
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `feat(preview): content-addressed preview cache with .done gating + LRU`

---

### Task 2: `parse_range` byte-range parser

**Files:**
- Create: `kj-controller/preview.py` (module + this function only for now)
- Test: `kj-controller/tests/unit/test_preview_range.py`

**Interfaces:** Produces `parse_range(header, size)`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_preview_range.py
import pytest
from preview import parse_range

@pytest.mark.parametrize("hdr,size,exp", [
    (None, 100, None),
    ("", 100, None),
    ("bytes=0-99", 100, (0, 99)),
    ("bytes=0-", 100, (0, 99)),
    ("bytes=50-", 100, (50, 99)),
    ("bytes=-20", 100, (80, 99)),     # suffix
    ("bytes=90-200", 100, (90, 99)),  # clamp end
    ("bytes=200-300", 100, None),     # unsatisfiable
    ("bytes=abc", 100, None),
])
def test_parse_range(hdr, size, exp):
    assert parse_range(hdr, size) == exp
```

- [ ] **Step 2: Run — expect FAIL** ("cannot import name parse_range").

- [ ] **Step 3: Implement** (start the file)

```python
# preview.py
"""Browser preview service: resolve a descriptor to a delivery mode and serve bytes."""
import os

def parse_range(header, size):
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    a, b = spec.split("-", 1)
    try:
        if a == "":                       # suffix: last b bytes
            n = int(b)
            if n <= 0: return None
            return (max(0, size - n), size - 1)
        start = int(a)
        end = int(b) if b != "" else size - 1
    except ValueError:
        return None
    if start >= size or start < 0:
        return None
    return (start, min(end, size - 1))
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `feat(preview): HTTP Range header parser`

---

### Task 3: TranscodeManager — ffmpeg→HLS, single active job

**Files:**
- Create: `kj-controller/preview_transcode.py`
- Test: `kj-controller/tests/unit/test_preview_transcode.py` (mock subprocess), plus an ffmpeg-real integration assertion folded into Task 5's service test.

**Interfaces:** Produces `TranscodeManager`, `TranscodeBusy`, `TranscodeError`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_preview_transcode.py
import os, types, pytest
import preview_transcode as pt

def test_ensure_hls_returns_cached_without_launch(tmp_path, monkeypatch):
    dest = tmp_path / "t"; dest.mkdir()
    (dest / "index.m3u8").write_text("#EXTM3U")
    launched = {"n": 0}
    monkeypatch.setattr(pt.subprocess, "Popen", lambda *a, **k: launched.__setitem__("n", launched["n"]+1))
    m = pt.TranscodeManager({})
    # Caller decides cache-hit; ensure_hls is only called on miss, but if playlist already
    # exists it must return it without relaunch:
    out = m.ensure_hls(str(tmp_path/"src.mkv"), str(dest), mark_done=lambda: None)
    assert out == str(dest / "index.m3u8") and launched["n"] == 0

def test_ensure_hls_launches_and_waits_for_playlist(tmp_path, monkeypatch):
    dest = tmp_path / "t2"
    class FakeProc:
        def __init__(self): self.returncode = None
        def poll(self): return None
        def wait(self): return 0
        def kill(self): pass
    def fake_popen(cmd, **k):
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "index.m3u8"), "w") as fh: fh.write("#EXTM3U")
        return FakeProc()
    monkeypatch.setattr(pt.subprocess, "Popen", fake_popen)
    m = pt.TranscodeManager({})
    out = m.ensure_hls(str(tmp_path/"src.mkv"), str(dest), mark_done=lambda: None)
    assert out.endswith("index.m3u8")
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# preview_transcode.py
"""On-demand exotic-video → HLS transcoder. Single active job; nice/ionice."""
import os, shutil, subprocess, threading, time

class TranscodeBusy(Exception): pass
class TranscodeError(Exception): pass

class TranscodeManager:
    def __init__(self, config):
        self.config = config or {}
        self.height = int(self.config.get("preview_transcode_height", 480))
        self.preset = self.config.get("preview_transcode_preset", "veryfast")
        self._lock = threading.Lock()
        self._active = None  # current Popen

    def _prefix(self):
        pre = []
        if shutil.which("nice"):  pre += ["nice", "-n", "19"]
        if shutil.which("ionice"): pre += ["ionice", "-c3"]
        return pre

    def _cmd(self, source_path, dest_dir):
        return self._prefix() + [
            "ffmpeg", "-nostdin", "-y", "-i", source_path,
            "-vf", f"scale=-2:{self.height}", "-c:v", "libx264",
            "-preset", self.preset, "-profile:v", "main", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "event",
            "-hls_flags", "append_list",
            "-hls_segment_filename", os.path.join(dest_dir, "seg-%d.ts"),
            os.path.join(dest_dir, "index.m3u8"),
        ]

    def kill_active(self):
        with self._lock:
            p = self._active
            if p and p.poll() is None:
                try: p.kill()
                except Exception: pass
            self._active = None

    def ensure_hls(self, source_path, dest_dir, mark_done):
        playlist = os.path.join(dest_dir, "index.m3u8")
        if os.path.exists(playlist):
            return playlist
        self.kill_active()                       # bump any in-progress job
        os.makedirs(dest_dir, exist_ok=True)
        if not shutil.which("ffmpeg"):
            raise TranscodeError("ffmpeg not available")
        proc = subprocess.Popen(self._cmd(source_path, dest_dir),
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._lock:
            self._active = proc

        def _watch(p=proc):
            rc = p.wait()
            if rc == 0:
                try: mark_done()
                except Exception: pass
        threading.Thread(target=_watch, daemon=True).start()

        deadline = time.time() + 15
        while time.time() < deadline:
            if os.path.exists(playlist):
                return playlist
            if proc.poll() is not None and proc.returncode != 0:
                raise TranscodeError("ffmpeg exited before producing a playlist")
            time.sleep(0.1)
        raise TranscodeError("transcode did not produce a playlist in time")
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `feat(preview): HLS transcode manager (single active job, nice/ionice)`

---

### Task 4: PreviewService.resolve (local) + token registry + serving accessors

**Files:**
- Modify: `kj-controller/preview.py` (add `PreviewService`)
- Test: `kj-controller/tests/unit/test_preview_service.py`

**Interfaces:** Consumes `PreviewCache`, `TranscodeManager`, `parse_range`, `playability.classify_kind`, `zip_playback.ZipPlayback`, `MediaIndex.validate_path`. Produces `PreviewService` (full).

**Decision logic (local):** `validate_path` → if None: `unavailable`/"file not found or outside allowed folders". Else `classify_kind`:
- `audio` → `native_audio`, mime by ext.
- `cdg_zip` → extract into `cdg_dir(local_key)` (cache; skip if present) → `cdg`.
- `video` → `_native_video_ok(path)`: ext in `{.mp4,.m4v,.mov,.webm}` AND ffprobe vcodec ∈ {h264,vp8,vp9,av1} AND acodec ∈ {aac,mp3,opus,vorbis,None} → `native_video`; else `hls` via TranscodeManager (key=`local_key`). `prefer_transcode=True` forces `hls`.
- `unknown`/missing → `unavailable`.

- [ ] **Step 1: Failing tests** (use a fake media with `validate_path`, monkeypatch ffprobe + transcode)

```python
# tests/unit/test_preview_service.py
import os, pytest
import preview
from preview import PreviewService

class FakeMedia:
    def __init__(self, roots): self.roots = roots
    def validate_path(self, p):
        rp = os.path.realpath(p)
        return rp if any(rp.startswith(os.path.realpath(r)) for r in self.roots) and os.path.exists(rp) else None

@pytest.fixture
def svc(tmp_path):
    cfg = {"preview_cache_dir": str(tmp_path/"cache"), "preview_cache_max_bytes": 10**9}
    return PreviewService(cfg, FakeMedia([str(tmp_path)]))

def test_local_audio(svc, tmp_path):
    f = tmp_path/"s.mp3"; f.write_bytes(b"ID3"+b"\0"*100)
    r = svc.resolve({"source":"local","file_path":str(f)})
    assert r["mode"] == "native_audio" and r["token"]
    info = svc.token_info(r["token"]); assert info["path"] == os.path.realpath(str(f))

def test_local_unknown_unavailable(svc, tmp_path):
    f = tmp_path/"s.xyz"; f.write_bytes(b"..")
    r = svc.resolve({"source":"local","file_path":str(f)})
    assert r["mode"] == "unavailable" and r["reason"]

def test_local_path_escape_blocked(svc):
    r = svc.resolve({"source":"local","file_path":"/etc/passwd"})
    assert r["mode"] == "unavailable"

def test_local_video_native(svc, tmp_path, monkeypatch):
    f = tmp_path/"v.mp4"; f.write_bytes(b"\0"*500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("h264","aac"))
    r = svc.resolve({"source":"local","file_path":str(f)})
    assert r["mode"] == "native_video"

def test_local_video_exotic_goes_hls(svc, tmp_path, monkeypatch):
    f = tmp_path/"v.mkv"; f.write_bytes(b"\0"*500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("hevc","aac"))
    monkeypatch.setattr(svc.transcoder, "ensure_hls",
                        lambda src, dest, mark_done: os.path.join(dest, "index.m3u8"))
    r = svc.resolve({"source":"local","file_path":str(f)})
    assert r["mode"] == "hls" and svc.token_info(r["token"])["hls_dir"]

def test_prefer_transcode_forces_hls(svc, tmp_path, monkeypatch):
    f = tmp_path/"v.mp4"; f.write_bytes(b"\0"*500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("h264","aac"))
    monkeypatch.setattr(svc.transcoder, "ensure_hls",
                        lambda src, dest, mark_done: os.path.join(dest, "index.m3u8"))
    r = svc.resolve({"source":"local","file_path":str(f),"prefer_transcode":True})
    assert r["mode"] == "hls"

def test_local_cdg_extracts(svc, tmp_path, monkeypatch):
    f = tmp_path/"song.zip"; f.write_bytes(b"PK\x03\x04rest")
    def fake_extract(self, zp):
        d = os.path.dirname(zp)
        # write into the cache via service; here just emulate ZipPlayback temp
        self._temp_dir = str(tmp_path/"ztmp"); os.makedirs(self._temp_dir, exist_ok=True)
        mp3 = os.path.join(self._temp_dir, "a.mp3"); open(mp3,"wb").write(b"x")
        open(os.path.join(self._temp_dir,"a.cdg"),"wb").write(b"c")
        return mp3
    monkeypatch.setattr(preview.ZipPlayback, "extract_and_get_mp3", fake_extract)
    monkeypatch.setattr(preview.ZipPlayback, "current_cdg_path",
                        lambda self: os.path.join(self._temp_dir, "a.cdg"))
    monkeypatch.setattr(preview.ZipPlayback, "cleanup", lambda self: None)
    r = svc.resolve({"source":"local","file_path":str(f)})
    assert r["mode"] == "cdg"
    assert svc.cdg_part_path(r["token"], "audio").endswith("audio.mp3")
    assert os.path.exists(svc.cdg_part_path(r["token"], "graphics"))
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** (append to `preview.py`)

```python
# preview.py  (append)
import json, mimetypes, shutil, subprocess, time, uuid
from preview_cache import PreviewCache
from preview_transcode import TranscodeManager, TranscodeError, TranscodeBusy
from playability import classify_kind
from zip_playback import ZipPlayback

_NATIVE_VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm"}
_NATIVE_VCODECS = {"h264", "vp8", "vp9", "av1"}
_NATIVE_ACODECS = {"aac", "mp3", "opus", "vorbis", None, ""}
_TOKEN_TTL_S = 3600

def _ffprobe_codecs(path):
    """Return (video_codec, audio_codec) lowercased, or (None, None) on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", path],
            capture_output=True, text=True, timeout=20)
        streams = json.loads(out.stdout or "{}").get("streams", [])
    except Exception:
        return (None, None)
    v = a = None
    for s in streams:
        if s.get("codec_type") == "video" and v is None: v = (s.get("codec_name") or "").lower()
        if s.get("codec_type") == "audio" and a is None: a = (s.get("codec_name") or "").lower()
    return (v, a)

def _mime_for(path):
    return mimetypes.guess_type(path)[0] or "application/octet-stream"

class PreviewService:
    def __init__(self, config, media):
        self.config = config or {}
        self.media = media
        root = self.config.get("preview_cache_dir") or os.path.join(
            self.config.get("download_folder", "/tmp"), ".preview-cache")
        self.cache = PreviewCache(root, self.config.get("preview_cache_max_bytes", 8 * 1024**3))
        self.transcoder = TranscodeManager(self.config)
        self._tokens = {}   # token -> entry

    # ---- resolve ---------------------------------------------------------
    def resolve(self, descriptor):
        self._gc()
        src = (descriptor or {}).get("source")
        try:
            if src == "youtube":
                url = descriptor.get("youtube_url")
                if not url: return self._unavailable("No YouTube URL")
                return {"mode": "youtube", "youtube_url": url,
                        "title": descriptor.get("title", "")}
            if src == "divebar":
                return self._resolve_divebar(descriptor)
            if src == "local":
                return self._resolve_local_path(descriptor.get("file_path"),
                                                descriptor, title=descriptor.get("title"))
            return self._unavailable("Unknown preview source")
        except TranscodeBusy:
            return self._unavailable("Another preview is transcoding — try again in a moment")
        except TranscodeError as e:
            return self._unavailable(f"Cannot transcode for preview: {e}")
        except Exception as e:                      # never leak a stack to the modal
            return self._unavailable(f"Preview failed: {e}")

    def _resolve_local_path(self, file_path, descriptor, title=None):
        if not file_path:
            return self._unavailable("No file path")
        real = self.media.validate_path(file_path)
        if not real and self.config.get("external_media_mount"):
            cand = os.path.realpath(file_path)
            mount = os.path.realpath(self.config["external_media_mount"])
            if cand.startswith(mount) and os.path.exists(cand): real = cand
        if not real:
            return self._unavailable("File not found or outside allowed folders")
        title = title or os.path.basename(real)
        kind = classify_kind(real)
        if kind == "audio":
            return self._mk(title, "native_audio", path=real, mime=_mime_for(real))
        if kind == "cdg_zip":
            return self._resolve_cdg(real, self.cache.local_key(real), title)
        if kind == "video":
            ext = os.path.splitext(real)[1].lower()
            vco, aco = _ffprobe_codecs(real)
            native = (not descriptor.get("prefer_transcode")
                      and ext in _NATIVE_VIDEO_EXTS and vco in _NATIVE_VCODECS
                      and aco in _NATIVE_ACODECS)
            if native:
                return self._mk(title, "native_video", path=real, mime=_mime_for(real))
            return self._resolve_hls(real, self.cache.local_key(real), title)
        return self._unavailable("Unsupported file type for preview")

    def _resolve_cdg(self, zip_path, key, title):
        cdir = self.cache.cdg_dir(key)
        audio_dst = os.path.join(cdir, "audio.mp3")
        graphics_dst = os.path.join(cdir, "graphics.cdg")
        if not (os.path.exists(audio_dst) and os.path.exists(graphics_dst)):
            os.makedirs(cdir, exist_ok=True)
            zp = ZipPlayback(self.config)
            try:
                mp3 = zp.extract_and_get_mp3(zip_path)
                cdg = zp.current_cdg_path() if mp3 else None
                if not mp3 or not cdg or not os.path.exists(cdg):
                    return self._unavailable("CDG zip missing .cdg/.mp3")
                shutil.copyfile(mp3, audio_dst); shutil.copyfile(cdg, graphics_dst)
            finally:
                try: zp.cleanup()
                except Exception: pass
        self.cache.touch(cdir)
        return self._mk(title, "cdg", audio=audio_dst, graphics=graphics_dst)

    def _resolve_hls(self, source_path, key, title):
        dest = self.cache.transcode_dir(key)
        if self.cache.is_done(key):
            self.cache.touch(dest)
        else:
            self.transcoder.ensure_hls(source_path, dest,
                                       mark_done=lambda: self.cache.mark_done(key))
            self.cache.evict_if_needed()
        return self._mk(title, "hls", hls_dir=dest)

    # ---- divebar (filled in Task 5) -------------------------------------
    def _resolve_divebar(self, descriptor):
        raise NotImplementedError  # Task 5

    # ---- token bookkeeping ----------------------------------------------
    def _mk(self, title, mode, **entry):
        token = uuid.uuid4().hex
        entry.update({"kind": mode, "created": time.time()})
        self._tokens[token] = entry
        return {"mode": mode, "token": token, "title": title or ""}

    def _unavailable(self, reason):
        return {"mode": "unavailable", "reason": reason, "title": ""}

    def token_info(self, token):
        return self._tokens.get(token)

    def cdg_part_path(self, token, part):
        e = self._tokens.get(token)
        if not e or e.get("kind") != "cdg": return None
        return e.get("audio") if part == "audio" else e.get("graphics") if part == "graphics" else None

    def hls_path(self, token, name):
        e = self._tokens.get(token)
        if not e or e.get("kind") != "hls": return None
        if name != "index.m3u8" and not _is_seg(name): return None
        return os.path.join(e["hls_dir"], name)

    def close(self, token=None):
        if token is None:
            self._tokens.clear(); self.transcoder.kill_active(); return
        self._tokens.pop(token, None)
        self.transcoder.kill_active()

    def _gc(self):
        now = time.time()
        for t, e in list(self._tokens.items()):
            if now - e.get("created", now) > _TOKEN_TTL_S:
                self._tokens.pop(t, None)

import re as _re
def _is_seg(name): return bool(_re.fullmatch(r"seg-\d+\.ts", name))
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `feat(preview): PreviewService resolve (local) + token registry`

---

### Task 5: PreviewService divebar branch (GCS blob → treat as local)

**Files:**
- Modify: `kj-controller/preview.py` (`_resolve_divebar`, `_ensure_divebar_blob`)
- Test: add to `tests/unit/test_preview_service.py`

**Interfaces:** Consumes `divebar.get_download_url`, `utils.divebar_ext`. Reuses `_resolve_local_path`.

**Logic:** `url = divebar.get_download_url(file_id, config)`; if None → unavailable. `ext = utils.divebar_ext(url, descriptor.get("format"))`. Blob path = `cache.gcsblob_path(file_id, f"blob{ext}")`. If absent, stream-download `url` → blob (requests, stream=True). Then `_resolve_local_path(blob, descriptor)` but **bypass `validate_path`** for cache-owned blobs (they're under our cache root, not media_folders) — call a shared `_classify_and_mode(real_path, descriptor, title)` that both local and divebar use after the path is trusted.

Refactor: extract the post-validation body of `_resolve_local_path` into `_classify_and_mode(real, descriptor, title)`; `_resolve_local_path` validates then calls it; `_resolve_divebar` ensures blob then calls it directly.

- [ ] **Step 1: Failing tests**

```python
# add to tests/unit/test_preview_service.py
def test_divebar_downloads_then_classifies(svc, tmp_path, monkeypatch):
    blob = b"\0"*300
    monkeypatch.setattr(preview.divebar, "get_download_url",
                        lambda fid, cfg: "https://storage.googleapis.com/x/y.mp4")
    monkeypatch.setattr(preview.utils, "divebar_ext", lambda url, fmt: ".mp4")
    monkeypatch.setattr(preview, "_download_to", lambda url, dst: open(dst,"wb").write(blob))
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("h264","aac"))
    r = svc.resolve({"source":"divebar","file_id":"FID","format":"mp4"})
    assert r["mode"] == "native_video"
    # second call hits cached blob (no re-download)
    monkeypatch.setattr(preview, "_download_to",
                        lambda url, dst: (_ for _ in ()).throw(AssertionError("re-downloaded")))
    r2 = svc.resolve({"source":"divebar","file_id":"FID","format":"mp4"})
    assert r2["mode"] == "native_video"

def test_divebar_no_url_unavailable(svc, monkeypatch):
    monkeypatch.setattr(preview.divebar, "get_download_url", lambda fid, cfg: None)
    r = svc.resolve({"source":"divebar","file_id":"FID"})
    assert r["mode"] == "unavailable"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** (edit `preview.py`)

Add imports `import requests`, `import divebar`, `import utils`. Add:

```python
def _download_to(url, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk: fh.write(chunk)
    os.replace(tmp, dst)
```

Refactor `_resolve_local_path` to:

```python
    def _resolve_local_path(self, file_path, descriptor, title=None):
        if not file_path: return self._unavailable("No file path")
        real = self.media.validate_path(file_path)
        if not real and self.config.get("external_media_mount"):
            cand = os.path.realpath(file_path)
            mount = os.path.realpath(self.config["external_media_mount"])
            if cand.startswith(mount) and os.path.exists(cand): real = cand
        if not real: return self._unavailable("File not found or outside allowed folders")
        return self._classify_and_mode(real, descriptor, title or os.path.basename(real),
                                        key=self.cache.local_key(real))
```

Move the `classify_kind(...)` body into `_classify_and_mode(self, real, descriptor, title, key)` (using the passed `key` for cdg/hls dirs instead of recomputing). Add:

```python
    def _resolve_divebar(self, descriptor):
        fid = descriptor.get("file_id")
        if not fid: return self._unavailable("No divebar file id")
        url = divebar.get_download_url(fid, self.config)
        if not url: return self._unavailable("Divebar file is not available to stream")
        ext = utils.divebar_ext(url, descriptor.get("format"))
        blob = self.cache.gcsblob_path(fid, f"blob{ext}")
        if not os.path.exists(blob):
            try: _download_to(url, blob)
            except Exception as e: return self._unavailable(f"Could not fetch from mirror: {e}")
        title = descriptor.get("title") or f"{descriptor.get('artist','')} - {descriptor.get('title','')}".strip(" -")
        return self._classify_and_mode(blob, descriptor, title or os.path.basename(blob),
                                       key=self.cache.gcs_key(fid))
```

- [ ] **Step 4: Run — expect PASS** (full `tests/unit/test_preview_service.py`).
- [ ] **Step 5: Commit** — `feat(preview): divebar GCS blob fetch reuses the local delivery path`

---

### Task 6: Flask routes + Range Response

**Files:**
- Modify: `kj-controller/routes.py` (add `/preview/*`), `kj-controller/app.py` (attach service), `kj-controller/config.py` (defaults)
- Test: `kj-controller/tests/integration/test_preview_routes.py`

**Interfaces:** Consumes `current_app.preview` (`PreviewService`). Endpoints:
- `POST /preview/resolve` body=descriptor → `jsonify(resolve(...))`.
- `GET /preview/stream/<token>` → 206/200 range serving of `token_info.path` with `token_info.mime`.
- `GET /preview/cdg/<token>/<part>` → `send_file(cdg_part_path)`; 404 if None.
- `GET /preview/hls/<token>/<path:name>` → `send_file(hls_path)` with m3u8/ts mime; 404 if None.
- `POST /preview/close` body `{token?}` → `close()`.

- [ ] **Step 1: Failing tests**

```python
# tests/integration/test_preview_routes.py
import os, pytest

def _put_media_file(flask_app, name, data):
    folder = flask_app.config_data["media_folders"][1] if hasattr(flask_app,"config_data") else None
    # use the download folder from config the app was built with:
    import flask
    folder = flask.current_app.config  # not used; build path from media index roots
    raise NotImplementedError

@pytest.fixture
def media_root(flask_app):
    # mock_config media_folders[1] is <tmp>/media
    return flask_app.media.config["media_folders"][1]

def test_resolve_audio_then_range_stream(flask_test_client, flask_app, media_root):
    p = os.path.join(media_root, "x.mp3"); open(p, "wb").write(b"abcdefghij")
    flask_app.media.scan()
    r = flask_test_client.post("/preview/resolve", json={"source":"local","file_path":p})
    body = r.get_json(); assert body["mode"] == "native_audio"
    tok = body["token"]
    full = flask_test_client.get(f"/preview/stream/{tok}")
    assert full.status_code == 200 and full.data == b"abcdefghij"
    rng = flask_test_client.get(f"/preview/stream/{tok}", headers={"Range":"bytes=2-4"})
    assert rng.status_code == 206 and rng.data == b"cde"
    assert rng.headers["Content-Range"] == "bytes 2-4/10"

def test_stream_bad_token_404(flask_test_client):
    assert flask_test_client.get("/preview/stream/nope").status_code == 404

def test_resolve_youtube(flask_test_client):
    r = flask_test_client.post("/preview/resolve",
                               json={"source":"youtube","youtube_url":"https://youtu.be/abc"})
    assert r.get_json()["mode"] == "youtube"
```

(If `flask_app.media.config` isn't the attribute name, read `media.py` and use the real one; the resolve+range+youtube assertions are the contract.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

`config.py` — add to the defaults dict:
```python
    "preview_cache_dir": "",          # "" → <download_folder>/.preview-cache
    "preview_cache_max_bytes": 8 * 1024 * 1024 * 1024,
    "preview_transcode_height": 480,
    "preview_transcode_preset": "veryfast",
```

`app.py` — in **both** `create_app` and `start_app`, after `flask_app.media = ...`:
```python
    from preview import PreviewService
    flask_app.preview = PreviewService(cfg, flask_app.media)
```

`routes.py` — add near other routes:
```python
from flask import send_file, Response, request, jsonify, current_app
from preview import parse_range

@routes_bp.route('/preview/resolve', methods=['POST'])
def preview_resolve():
    return jsonify(current_app.preview.resolve(request.get_json(silent=True) or {}))

@routes_bp.route('/preview/close', methods=['POST'])
def preview_close():
    current_app.preview.close((request.get_json(silent=True) or {}).get('token'))
    return jsonify({"ok": True})

@routes_bp.route('/preview/stream/<token>', methods=['GET'])
def preview_stream(token):
    info = current_app.preview.token_info(token)
    if not info or info.get("kind") not in ("native_video", "native_audio"):
        return ("Not found", 404)
    path = info["path"]; mime = info.get("mime", "application/octet-stream")
    size = os.path.getsize(path)
    rng = parse_range(request.headers.get("Range"), size)
    if rng is None:
        if request.headers.get("Range"):
            resp = Response(status=416); resp.headers["Content-Range"] = f"bytes */{size}"; return resp
        return _full_response(path, size, mime)
    start, end = rng
    length = end - start + 1
    def gen():
        with open(path, "rb") as fh:
            fh.seek(start); remaining = length
            while remaining > 0:
                chunk = fh.read(min(262144, remaining))
                if not chunk: break
                remaining -= len(chunk); yield chunk
    resp = Response(gen(), status=206, mimetype=mime)
    resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(length)
    return resp

def _full_response(path, size, mime):
    def gen():
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(262144)
                if not chunk: break
                yield chunk
    resp = Response(gen(), status=200, mimetype=mime)
    resp.headers["Accept-Ranges"] = "bytes"; resp.headers["Content-Length"] = str(size)
    return resp

@routes_bp.route('/preview/cdg/<token>/<part>', methods=['GET'])
def preview_cdg(token, part):
    p = current_app.preview.cdg_part_path(token, part)
    if not p or not os.path.exists(p): return ("Not found", 404)
    mime = "audio/mpeg" if part == "audio" else "application/octet-stream"
    return send_file(p, mimetype=mime, conditional=True)

@routes_bp.route('/preview/hls/<token>/<path:name>', methods=['GET'])
def preview_hls(token, name):
    p = current_app.preview.hls_path(token, name)
    if not p or not os.path.exists(p): return ("Not found", 404)
    mime = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
    return send_file(p, mimetype=mime, conditional=True)
```

(Ensure `import os` present in routes.py.)

- [ ] **Step 4: Run — expect PASS.** Also run the **real-ffmpeg integration check** (skippable): add `test_real_transcode_caches` that writes a tiny mkv via ffmpeg, resolves, asserts `mode=="hls"`, GETs the playlist 200, then asserts a second resolve is a cache hit (no new ffmpeg) — `@pytest.mark.skipif(shutil.which("ffmpeg") is None)`.
- [ ] **Step 5: Commit** — `feat(preview): /preview routes (resolve, range stream, cdg, hls, close) + app wiring`

---

### Task 7: CDG renderer `static/cdg.js` + node-driven test

**Files:**
- Create: `kj-controller/static/cdg.js`
- Test: `kj-controller/tests/unit/test_cdg_renderer.py` (shells `node`)

**Interfaces:** Produces `CDGPlayer` (see JS interfaces). Implements the CD+G spec: 24-byte packets @300/s; instructions Memory Preset(1), Border Preset(2), Tile Block(6)/XOR(38), Scroll Preset(20)/Copy(24), Define Transparent(28), Load CLUT lo(30)/hi(31). 300×216 indexed buffer, 16-color 12-bit CLUT (4-bit/chan ×17).

- [ ] **Step 1: Failing test** (pure-logic pixel assertions via node)

```python
# tests/unit/test_cdg_renderer.py
import shutil, subprocess, os, textwrap, pytest

CDG = os.path.join(os.path.dirname(__file__), "..", "..", "static", "cdg.js")

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_clut_and_memory_preset_and_tile():
    script = textwrap.dedent(f"""
      const {{CDGPlayer}} = require({os.path.relpath(CDG).__repr__()});
      const P = []; // build packets
      function pkt(inst, data) {{
        const b = new Uint8Array(24); b[0]=0x09; b[1]=inst;
        for (let i=0;i<16;i++) b[4+i]=data[i]||0; return b;
      }}
      // Load CLUT lo: color0=black(0,0,0), color1=white(15,15,15)
      // hi byte = RRRRGG (6 bits), lo byte = GGBBBB; white => hi=0x3F, lo=0x3F
      const lo = new Array(16).fill(0);
      lo[0]=0;lo[1]=0;          // color0 black
      lo[2]=0x3F;lo[3]=0x3F;    // color1 white
      // Memory preset to color0, then tile block at row0,col0 filling color1 row pattern 0x3F
      const data = new Uint8Array(24*3);
      const p1 = pkt(30, lo);                       // load clut lo
      const mp = pkt(1, [0,0]);                      // memory preset color0
      const tb = pkt(6, [0,1,0,0, 0x3F,0,0,0,0,0,0,0,0,0,0,0]); // color0=0,color1=1,row0,col0,first pixrow all set
      let all = []; [p1,mp,tb].forEach(p=>all.push(...p));
      const buf = Uint8Array.from(all);
      const pl = new CDGPlayer(null); pl.load(buf); pl.renderAt(1.0);  // apply all 3 packets
      const [r,g,b,a] = pl.getPixelRGBA(0,0);   // top-left tile pixel0 => color1 white
      const [r2] = pl.getPixelRGBA(0,11);       // a non-set pixel row => color0 black
      if (!(r===255 && g===255 && b===255)) {{ console.error('pix0',r,g,b); process.exit(1); }}
      if (r2!==0) {{ console.error('pix bg', r2); process.exit(1); }}
      console.log('OK');
    """)
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
```

- [ ] **Step 2: Run — expect FAIL** (cdg.js missing).

- [ ] **Step 3: Implement** `static/cdg.js`

```js
// cdg.js — minimal CD+G decoder/renderer. UMD: window.CDGPlayer + module.exports.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.CDGPlayer = factory().CDGPlayer;
}(typeof self !== 'undefined' ? self : this, function () {
  const W = 300, H = 216, TW = 6, TH = 12;
  const CMD = 0x09, M = 0x3F;
  function CDGPlayer(canvas) {
    this.canvas = canvas || null;
    this.ctx = canvas ? canvas.getContext('2d') : null;
    this.width = W; this.height = H;
    this.reset();
  }
  CDGPlayer.prototype.reset = function () {
    this.packets = this.packets || [];
    this.pixels = new Uint8Array(W * H);     // colour indices
    this.clut = new Array(16).fill(0).map(() => [0, 0, 0]);
    this.lastIdx = -1;
    this.borderIndex = 0;
    this.hOffset = 0; this.vOffset = 0;
    if (this.canvas) this.image = this.ctx.createImageData(W, H);
  };
  CDGPlayer.prototype.load = function (bytes) {
    const n = Math.floor(bytes.length / 24);
    this.packets = new Array(n);
    for (let i = 0; i < n; i++) this.packets[i] = bytes.subarray(i * 24, i * 24 + 24);
    this.reset();
  };
  CDGPlayer.prototype.renderAt = function (timeSeconds) {
    let target = Math.floor(timeSeconds * 300);
    if (target >= this.packets.length) target = this.packets.length - 1;
    if (target < this.lastIdx) { this.reset(); }   // backward seek → replay
    for (let i = this.lastIdx + 1; i <= target; i++) this._apply(this.packets[i]);
    this.lastIdx = target;
    this._paint();
  };
  CDGPlayer.prototype._apply = function (p) {
    if ((p[0] & M) !== CMD) return;
    const inst = p[1] & M, d = p; // d[4..19] payload
    switch (inst) {
      case 1: { const c = d[4] & 0x0F; this.pixels.fill(c); this.borderIndex = c; break; }   // memory preset
      case 2: { this.borderIndex = d[4] & 0x0F; break; }                                     // border preset
      case 30: this._clut(d, 0); break;                                                      // load clut lo 0-7
      case 31: this._clut(d, 8); break;                                                      // load clut hi 8-15
      case 6: this._tile(d, false); break;                                                   // tile normal
      case 38: this._tile(d, true); break;                                                   // tile xor
      case 20: this._scroll(d, false); break;                                                // scroll preset
      case 24: this._scroll(d, true); break;                                                 // scroll copy
      default: break;                                                                        // 28 transparency: ignore for preview
    }
  };
  CDGPlayer.prototype._clut = function (d, base) {
    for (let i = 0; i < 8; i++) {
      const hi = d[4 + i * 2] & M, lo = d[4 + i * 2 + 1] & M;
      const r = (hi >> 2) & 0x0F;
      const g = ((hi & 0x03) << 2) | ((lo >> 4) & 0x03);
      const b = lo & 0x0F;
      this.clut[base + i] = [r * 17, g * 17, b * 17];
    }
  };
  CDGPlayer.prototype._tile = function (d, xor) {
    const c0 = d[4] & 0x0F, c1 = d[5] & 0x0F;
    const row = (d[6] & M) * TH, col = (d[7] & M) * TW;
    for (let y = 0; y < TH; y++) {
      const bits = d[8 + y] & M;
      for (let x = 0; x < TW; x++) {
        const on = (bits >> (5 - x)) & 1;
        const px = col + x, py = row + y;
        if (px < 0 || px >= W || py < 0 || py >= H) continue;
        const idx = py * W + px;
        if (xor) this.pixels[idx] = this.pixels[idx] ^ (on ? c1 : c0);
        else this.pixels[idx] = on ? c1 : c0;
      }
    }
  };
  CDGPlayer.prototype._scroll = function (d, copy) {
    const hCmd = (d[5] & 0x30) >> 4, vCmd = (d[6] & 0x30) >> 4;
    const dx = hCmd === 1 ? TW : hCmd === 2 ? -TW : 0;
    const dy = vCmd === 1 ? TH : vCmd === 2 ? -TH : 0;
    if (dx === 0 && dy === 0) return;
    const src = this.pixels.slice();
    const fill = d[4] & 0x0F;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      let sx = x - dx, sy = y - dy;
      if (copy) { sx = (sx + W) % W; sy = (sy + H) % H; }
      this.pixels[y * W + x] = (sx >= 0 && sx < W && sy >= 0 && sy < H) ? src[sy * W + sx] : fill;
    }
  };
  CDGPlayer.prototype.getPixelRGBA = function (x, y) {
    const c = this.clut[this.pixels[y * W + x]] || [0, 0, 0];
    return [c[0], c[1], c[2], 255];
  };
  CDGPlayer.prototype._paint = function () {
    if (!this.ctx) return;
    const img = this.image, px = this.pixels, clut = this.clut;
    for (let i = 0; i < W * H; i++) {
      const c = clut[px[i]] || [0, 0, 0], o = i * 4;
      img.data[o] = c[0]; img.data[o + 1] = c[1]; img.data[o + 2] = c[2]; img.data[o + 3] = 255;
    }
    this.ctx.putImageData(img, 0, 0);
  };
  return { CDGPlayer: CDGPlayer };
}));
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `feat(preview): vanilla CD+G canvas renderer + node-driven test`

---

### Task 8: `preview.js` — modal, resolve dispatch, players, button factory

**Files:**
- Create: `kj-controller/static/preview.js`
- (Modal markup added in Task 9.)

**Interfaces:** Produces `openPreview`, `previewButtonHtml`, `closePreview` on `window`. Uses `escHtml` (from app.js, loaded first). Lazy-loads `hls.js`.

Behaviour:
- `openPreview(descriptor)`: show modal + spinner; `POST /preview/resolve`; on result switch on `mode`:
  - `native_video` → `<video controls autoplay src=/preview/stream/<token>>`.
  - `native_audio` → `<audio controls autoplay src=/preview/stream/<token>>`.
  - `cdg` → `<canvas>` + hidden? no — visible `<audio controls autoplay src=/preview/cdg/<token>/audio>`; fetch `/preview/cdg/<token>/graphics` as arrayBuffer → `new CDGPlayer(canvas).load(...)`; drive `renderAt(audio.currentTime)` on `requestAnimationFrame` while not paused/seeking; native audio scrub seeks.
  - `hls` → if `video.canPlayType('application/vnd.apple.mpegurl')` use native src; else lazy-load `/static/vendor/hls.min.js`, `new Hls()`, `loadSource(/preview/hls/<token>/index.m3u8)`, attach to `<video>`. On native `<video>` `error` for a GCS native attempt (descriptor.source==='divebar' && mode was native_video) → re-resolve with `prefer_transcode:true`.
  - `youtube` → iframe `https://www.youtube.com/embed/<id>?autoplay=1` (extract id; reuse app.js `extractYouTubeId` if present on window, else inline regex).
  - `unavailable` → show `reason` text, no player.
- Footer action from `descriptor.link_context`: render a button calling the existing flow (e.g. `selectRotSearchResult` / link). If absent, no footer button.
- `closePreview()`: stop players, cancel rAF, clear modal body, `POST /preview/close {token}`, hide modal. Bind to backdrop click + close button + Escape.

- [ ] **Step 1:** Create `preview.js` implementing the above (full code; no placeholders). Include a tiny pure helper `previewModeFor(result)` returning the element-type string so it’s testable, and export `{previewModeFor}` under `module.exports` when in node.
- [ ] **Step 2:** Node test `tests/unit/test_preview_js.py` (skip if no node): require preview.js in a stubbed-global context, assert `previewButtonHtml({source:'local',file_path:'/a/b.mp4'})` contains `▶` and escaped path, and `previewModeFor({mode:'cdg'})==='cdg'`.
- [ ] **Step 3:** Run — expect PASS.
- [ ] **Step 4: Commit** — `feat(preview): browser preview modal + player dispatch (preview.js)`

---

### Task 9: Modal markup, styles, vendored hls.js, and wire buttons into surfaces

**Files:**
- Modify: `kj-controller/templates/index.html`, `kj-controller/static/style.css`, `kj-controller/static/app.js`
- Create: `kj-controller/static/vendor/hls.min.js`

- [ ] **Step 1: Vendor hls.js** — `curl -L https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js -o kj-controller/static/vendor/hls.min.js` (pin 1.5.17). Verify it's ~100KB+ JS, not an error page.
- [ ] **Step 2: index.html** — add before `</body>`, after app.js:
  ```html
  <div id="preview-modal" class="modal-backdrop hidden" onclick="if(event.target===this) closePreview()">
    <div class="modal-content preview-modal-content">
      <div class="modal-header">
        <h3 id="preview-modal-title">Preview</h3>
        <button class="modal-close" onclick="closePreview()">&times;</button>
      </div>
      <div id="preview-modal-body" class="preview-body"></div>
      <div id="preview-modal-footer" class="preview-footer"></div>
    </div>
  </div>
  <script src="/static/cdg.js?v={{ config.get('APP_VERSION','') }}"></script>
  <script src="/static/preview.js?v={{ config.get('APP_VERSION','') }}"></script>
  ```
- [ ] **Step 3: style.css** — add `.preview-modal-content{max-width:600px;width:90vw}`, `.preview-body video,.preview-body canvas{width:100%;max-width:560px;background:#000;border-radius:6px}`, `.preview-body audio{width:100%}`, `.preview-footer{margin-top:10px;display:flex;gap:8px;justify-content:flex-end}`, an `.preview-unavailable` muted style, and a `.preview-btn` pill consistent with existing row buttons.
- [ ] **Step 4: app.js wiring** — add a `▶︎` preview button via `previewButtonHtml(...)`:
  - `renderRotLocalRow` (≈`app.js:5666`): descriptor `{source:'local',file_path:match.path,title:...}`.
  - `renderRotKnRow` (≈`app.js:5693`): if downloaded → `{source:'local',file_path}`; else if `track.divebar` → `{source:'divebar',file_id,format}`; else `{source:'youtube',youtube_url}`.
  - `renderRotDivebarRow` (≈`app.js:5760`): `{source:'divebar',file_id:dv.file_id,format:dv.format,title:...}`.
  - `createMediaItemLi` (≈`app.js:688`): append a preview button (`{source:'local',file_path:item.file_path}`) next to Copy; stop propagation so it doesn't trigger row `playMedia`.
  Each button calls `event.stopPropagation(); openPreview(<descriptor>)`.
- [ ] **Step 5:** `pre-commit` JS syntax hook must pass; load the page locally (proxy prod or fixture) and smoke one preview of each kind feasible locally. Commit — `feat(preview): wire preview buttons into link search + available songs; modal markup, styles, vendored hls.js`

---

### Task 10: Version bump + docs

**Files:** `kj-controller/pyproject.toml`, `docs/ARCHITECTURE.md`, `CHANGELOG`/handoff.

- [ ] **Step 1:** Bump `pyproject.toml` version (next minor, e.g. `0.43.x`→`0.44.0`). 
- [ ] **Step 2:** Add the new modules to the `docs/ARCHITECTURE.md` module table + a short "Browser Preview" section (modes table, cache, non-interference).
- [ ] **Step 3:** Run full `pytest`; expect green (CDG/ffmpeg tests skip if tools missing — they're present locally).
- [ ] **Step 4: Commit** — `chore(preview): version bump + architecture docs`.

---

## Self-Review notes

- **Spec coverage:** native/exotic/cdg/audio/youtube/divebar modes (T4/T5), cache+`.done`+LRU (T1), transcode capped+single-job+nice (T3), range serving (T2/T6), CDG canvas renderer (T7), modal+seek+footer action (T8/T9), surfaces wired (T9), error/unavailable reasons (T4 `_unavailable`, T6 404s), tests (every task). Cache pre-warm = free side effect of `.done` reuse. ✓
- **Non-interference:** preview never calls the player/VLC/mpv; only reads files + ffmpeg(nice). ✓
- **Type consistency:** `resolve→{mode,token,...}`, `token_info` entry shape, `cdg_part_path(part in {audio,graphics})`, `hls_path(name)` consistent across T4/T6/T8. `_classify_and_mode(real, descriptor, title, key)` shared by T4/T5. ✓
