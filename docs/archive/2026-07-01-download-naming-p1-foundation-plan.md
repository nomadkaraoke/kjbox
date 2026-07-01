# Download Naming — Phase 1 (Identity Foundation + Master GCS Sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the canonical media-identity store (stable `media_id` → artist/title in SQLite) wired into the media scan, plus an automated 5-minute GCS sync that keeps the on-device Nomad master catalog current — delivering immediate library freshness with zero LLM dependency.

**Architecture:** A new pure `naming.py` module classifies a file's source and derives a stable, source-prefixed `media_id` + a deterministic best-effort artist/title. A new `media_library.py` SQLite store (mirroring `RotationStore`'s per-thread-connection pattern) persists the canonical record keyed by `media_id`, so identity survives renames. `MediaIndex.scan()` upserts a row per media file. A standalone `scripts/sync_masters.py`, driven by a systemd timer, runs `gcloud storage rsync` (additive, read-only SA) from the GCS masters prefix into `downloads/NOMAD-720p/` and pokes `/rescan` when files change.

**Tech Stack:** Python 3, Flask, sqlite3 (WAL), `requests`, `gcloud storage` CLI, systemd timer, pytest.

## Global Constraints

- **Design spec (authoritative):** `docs/archive/2026-06-30-download-naming-normalization-design.md`. This plan implements **only Phase 1** of its suggested phasing.
- **`media_id` scheme (verbatim):** `yt-<11-char-video-id>` · `db-<brand>-<file_id>` (fallback `db-<brand>-<hash8>`) · `gen-<job_id[:8]>` · `nomad-<disc#>` · `up-<sha1(file)[:8]>`.
- **Format order:** `Artist - Title` (Phase 1 stores canonical artist/title in the DB; the on-disk slug rename and rotation-field flip are Phase 3/4 — do NOT rename download files or change rotation builders in this phase).
- **Root paths (post-restructure):** download root is `/opt/nomad/downloads`; masters mirror is `/opt/nomad/downloads/NOMAD-720p`; GCS source is `gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/`.
- **`NOMAD-720p` is exempt from slug renaming** — it keeps GCS-native `NOMAD-#### - Artist - Title.mp4` names so rsync stays cheap.
- **Master sync auth:** a dedicated **read-only** service account (`roles/storage.objectViewer` on `nomadkaraoke-divebar-files`); never a personal account.
- **Offline resilience:** every network call degrades gracefully (try/except → log + continue), matching `divebar.py`/`gen_poller.py`. The sync failing must never crash the app or block playback.
- **Production safety:** NomadPC is a live device; kjbox autodeploy is OFF. Backend changes need a manual off-show service restart. The one-time restructure runs off-show after DB backups.
- **Testing:** kjbox has no pytest CI (only `security.yml`); run `cd kj-controller && pytest` locally. New unit tests live in `kj-controller/tests/unit/`.
- **Reuse, don't reinvent:** normalization via `text_normalize.normalize`; existing parsers `utils.parse_youtube_filename`, `catalog.parse_karaoke_filename`, `utils.sanitize_filename_part`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `kj-controller/naming.py` (new) | Pure functions: source classification, `media_id` derivation, deterministic artist/title parse (noise stripping), slug builder, `[media_id]` token extraction, content hashing. No I/O except `content_hash(path)`. |
| `kj-controller/media_library.py` (new) | `MediaLibraryStore`: SQLite CRUD keyed by `media_id`; computes `*_norm` on write. Mirrors `RotationStore` connection handling. |
| `kj-controller/config.py` (modify) | Add `media_db_path` + `master_sync_*` config keys with defaults. |
| `kj-controller/media.py` (modify) | `MediaIndex.__init__` takes an optional `media_library`; `scan()` resolves `media_id` and upserts a row per file. |
| `kj-controller/app.py` (modify) | Construct `MediaLibraryStore`, attach to app, pass into both `MediaIndex(...)` call sites. |
| `kj-controller/scripts/sync_masters.py` (new) | Run `gcloud storage rsync` (additive) GCS→`NOMAD-720p`; POST `/rescan` on change; flock-guarded; SA key via env. |
| `kj-controller/deploy/nomad-master-sync.service` + `.timer` (new) | systemd oneshot service + 5-min timer. |
| `kj-controller/tests/unit/test_naming.py` (new) | Unit tests for `naming.py` using real sampled filenames. |
| `kj-controller/tests/unit/test_media_library.py` (new) | Unit tests for `MediaLibraryStore`. |
| `kj-controller/tests/unit/test_sync_masters.py` (new) | Unit tests for the sync script (mock subprocess + requests). |
| `kj-controller/tests/unit/test_media_library_scan.py` (new) | Integration test: scan populates the store. |
| `docs/MASTER-SYNC.md` (new) | Operational runbook: root restructure, SA creation, systemd install. |

---

### Task 1: Config keys for the identity store + master sync

**Files:**
- Modify: `kj-controller/config.py:28-75` (the `defaults` dict in `load_config`)
- Test: `kj-controller/tests/unit/test_config_media_keys.py` (new)

**Interfaces:**
- Produces: config keys `media_db_path` (str), `master_sync_source` (str), `master_sync_dest` (str), `master_sync_credentials_file` (str), `master_sync_enabled` (bool), consumed by Tasks 3, 5, 6.

- [ ] **Step 1: Write the failing test**

```python
# kj-controller/tests/unit/test_config_media_keys.py
from config import load_config


def test_media_and_sync_defaults_present(tmp_path):
    cfg = load_config(str(tmp_path / "nonexistent.json"))
    assert cfg["media_db_path"].endswith("media_library.db")
    assert cfg["master_sync_source"] == (
        "gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/"
    )
    assert cfg["master_sync_dest"] == ""          # "" -> derived under download_folder
    assert cfg["master_sync_credentials_file"] == ""
    assert cfg["master_sync_enabled"] is False    # opt-in; on-device config turns it on
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_config_media_keys.py -v`
Expected: FAIL (KeyError on `media_db_path`).

- [ ] **Step 3: Add the keys to the defaults dict**

In `config.py`, inside `load_config`'s `defaults` dict (after the `external_catalog_db` line ~47), add:

```python
        # Canonical media-identity store (stable media_id -> artist/title).
        "media_db_path": os.path.join(APP_DIR, 'media_library.db'),
        # Automated GCS sync of the Nomad master catalog into the NOMAD-720p mirror.
        "master_sync_source": "gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/",
        "master_sync_dest": "",  # "" -> <download_folder>/NOMAD-720p
        "master_sync_credentials_file": "",  # read-only SA key (storage.objectViewer)
        "master_sync_enabled": False,  # enabled per-device in config.json
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_config_media_keys.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/config.py kj-controller/tests/unit/test_config_media_keys.py
git commit -m "feat(config): add media_library + master-sync config keys"
```

---

### Task 2: `naming.py` — source classification + media_id + deterministic parse

**Files:**
- Create: `kj-controller/naming.py`
- Test: `kj-controller/tests/unit/test_naming.py`

**Interfaces:**
- Consumes: `utils.parse_youtube_filename`, `utils.sanitize_filename_part`, `catalog.parse_karaoke_filename`.
- Produces (relied on by Tasks 3, 4):
  - `SOURCE_YOUTUBE, SOURCE_COMMUNITY, SOURCE_GEN, SOURCE_MASTER, SOURCE_UPLOAD` (str constants).
  - `classify_source(filename: str) -> str`
  - `media_id_for(source: str, source_ref: str) -> str`
  - `strip_karaoke_noise(text: str) -> str`
  - `parse_identity(filename: str, channel: str | None = None) -> dict` returning keys
    `{source, source_ref, artist, title, confidence, needs_review, parse_method}`.
  - `extract_media_id(filename: str) -> str | None`
  - `build_slug_filename(artist: str, title: str, media_id: str, ext: str) -> str`
  - `content_hash(path: str) -> str` (sha1 hex, first 8 chars).

- [ ] **Step 1: Write the failing tests** (real sampled names)

```python
# kj-controller/tests/unit/test_naming.py
import naming


def test_classify_source():
    assert naming.classify_source("NOMAD-0729 - Cher - Believe.mp4") == naming.SOURCE_MASTER
    assert naming.classify_source(
        "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4"
    ) == naming.SOURCE_YOUTUBE
    assert naming.classify_source("divebar__WTF - Queen - Bohemian.mp4") == naming.SOURCE_COMMUNITY
    assert naming.classify_source("divebar__GEN-1a2b3c4d - Cher - Believe.mp4") == naming.SOURCE_GEN
    assert naming.classify_source("GEN-1a2b3c4d - Cher - Believe.mp4") == naming.SOURCE_GEN
    assert naming.classify_source("Some Random Upload.mp4") == naming.SOURCE_UPLOAD


def test_media_id_for():
    assert naming.media_id_for(naming.SOURCE_YOUTUBE, "UM1XiyBmhM") == "yt-UM1XiyBmhM"
    assert naming.media_id_for(naming.SOURCE_MASTER, "0729") == "nomad-0729"
    assert naming.media_id_for(naming.SOURCE_GEN, "1a2b3c4d") == "gen-1a2b3c4d"


def test_strip_karaoke_noise():
    assert naming.strip_karaoke_noise("Bella Kay - iloveit (Karaoke Version)") == "Bella Kay - iloveit"
    assert naming.strip_karaoke_noise("River KARAOKE") == "River"
    assert naming.strip_karaoke_noise("the grudge (Final Karaoke Lossy 4k)") == "the grudge"
    assert naming.strip_karaoke_noise("Yesterday [karaoke]") == "Yesterday"


def test_parse_identity_master_is_clean_and_confident():
    r = naming.parse_identity("NOMAD-0729 - Cher - Believe.mp4")
    assert r["source"] == naming.SOURCE_MASTER
    assert r["source_ref"] == "0729"
    assert r["artist"] == "Cher"
    assert r["title"] == "Believe"
    assert r["needs_review"] == 0
    assert r["parse_method"] == "master"


def test_parse_identity_youtube_best_effort_needs_review():
    r = naming.parse_identity(
        "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4"
    )
    assert r["source"] == naming.SOURCE_YOUTUBE
    assert r["source_ref"] == "UM1XiyBmhM"
    # deterministic best-effort: split on ' - ', noise stripped, order UNRESOLVED
    assert r["artist"] == "Bella Kay"
    assert r["title"] == "iloveit"
    assert r["needs_review"] == 1          # order/accuracy unverified until LLM (Phase 2)
    assert r["parse_method"] == "deterministic"


def test_parse_identity_community():
    r = naming.parse_identity("divebar__WTF - Queen - Bohemian Rhapsody.mp4")
    assert r["source"] == naming.SOURCE_COMMUNITY
    assert r["artist"] == "Queen"
    assert r["title"] == "Bohemian Rhapsody"
    assert r["source_ref"].startswith("WTF-")   # brand-<hash8> fallback (no file_id in name)


def test_extract_media_id():
    assert naming.extract_media_id("Bella Kay - iloveit [yt-UM1XiyBmhM].mp4") == "yt-UM1XiyBmhM"
    assert naming.extract_media_id("NOMAD-0729 - Cher - Believe.mp4") is None


def test_build_slug_filename_sanitizes_and_bounds_length():
    out = naming.build_slug_filename("AC/DC", "T.N.T", "yt-abc12345678", ".mp4")
    assert out.endswith(" [yt-abc12345678].mp4")
    assert "/" not in out
    assert len(out.encode("utf-8")) <= 255
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_naming.py -v`
Expected: FAIL (`ModuleNotFoundError: naming`).

- [ ] **Step 3: Implement `naming.py`**

```python
# kj-controller/naming.py
"""Pure source-classification, media_id derivation, and deterministic
best-effort artist/title parsing for downloaded/library media.

No I/O except content_hash(path). The LLM refinement layer (Phase 2) upgrades
low-confidence results; this module never calls the network.
"""

import hashlib
import os
import re

from utils import parse_youtube_filename, sanitize_filename_part
from catalog import parse_karaoke_filename

SOURCE_YOUTUBE = "youtube"
SOURCE_COMMUNITY = "community"
SOURCE_GEN = "gen"
SOURCE_MASTER = "master"
SOURCE_UPLOAD = "upload"

_MASTER_RE = re.compile(r"^NOMAD-(\d+)\b", re.IGNORECASE)
_GEN_RE = re.compile(r"\bGEN-([0-9a-f]{4,})\b", re.IGNORECASE)
_YT_RE = re.compile(r"^[A-Za-z0-9_-]{11}__")
_MEDIA_ID_RE = re.compile(r"\[([a-z]+-[^\]]+)\]\.[^.]+$")

# Ordered noise patterns stripped from a title fragment (case-insensitive).
_NOISE_RES = [
    re.compile(r"\((?:final\s+)?karaoke[^)]*\)", re.IGNORECASE),
    re.compile(r"\[(?:final\s+)?karaoke[^\]]*\]", re.IGNORECASE),
    re.compile(r"\bkaraoke\b", re.IGNORECASE),
    re.compile(r"\b(?:official\s+video|lyrics?|instrumental|cover)\b", re.IGNORECASE),
    re.compile(r"_\s*karafun.*$", re.IGNORECASE),
]


def strip_karaoke_noise(text):
    """Remove karaoke-marker noise and collapse whitespace/leftover separators."""
    out = text or ""
    for rx in _NOISE_RES:
        out = rx.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip(" -_•|")
    return out.strip()


def classify_source(filename):
    """Best-effort source classification from the filename alone."""
    name = os.path.basename(filename or "")
    if _MASTER_RE.match(name):
        return SOURCE_MASTER
    if _GEN_RE.search(name):
        return SOURCE_GEN
    if _YT_RE.match(name):
        return SOURCE_YOUTUBE
    if name.startswith("divebar__"):
        return SOURCE_COMMUNITY
    return SOURCE_UPLOAD


def media_id_for(source, source_ref):
    prefix = {
        SOURCE_YOUTUBE: "yt",
        SOURCE_COMMUNITY: "db",
        SOURCE_GEN: "gen",
        SOURCE_MASTER: "nomad",
        SOURCE_UPLOAD: "up",
    }[source]
    return f"{prefix}-{source_ref}"


def extract_media_id(filename):
    """Return the embedded [media_id] token from a slug filename, or None."""
    m = _MEDIA_ID_RE.search(os.path.basename(filename or ""))
    return m.group(1) if m else None


def _hash8(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]


def content_hash(path):
    """sha1 of file bytes, first 8 hex chars (stable id for keyless uploads)."""
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def build_slug_filename(artist, title, media_id, ext):
    """`<Artist> - <Title> [<media_id>]<ext>`, sanitized and <=255 bytes."""
    a = sanitize_filename_part(artist or "").strip()
    t = sanitize_filename_part(title or "").strip()
    stem = " - ".join(p for p in (a, t) if p) or "unknown"
    suffix = f" [{media_id}]{ext}"
    budget = 255 - len(suffix.encode("utf-8"))
    stem_b = stem.encode("utf-8")[:budget]
    stem = stem_b.decode("utf-8", "ignore").strip()
    return f"{stem}{suffix}"


def parse_identity(filename, channel=None):
    """Deterministic best-effort identity. Returns a dict:
    {source, source_ref, artist, title, confidence, needs_review, parse_method}.
    """
    name = os.path.basename(filename or "")
    source = classify_source(name)

    if source == SOURCE_MASTER:
        m = _MASTER_RE.match(name)
        disc = m.group(1)
        _disc_id, artist, title = parse_karaoke_filename(name)
        return {
            "source": source, "source_ref": disc,
            "artist": artist, "title": title,
            "confidence": 1.0, "needs_review": 0, "parse_method": "master",
        }

    if source == SOURCE_YOUTUBE:
        parsed = parse_youtube_filename(name)
        vid = parsed[0] if parsed else ""
        title_str = parsed[2] if parsed else os.path.splitext(name)[0]
        clean = strip_karaoke_noise(title_str.replace(" _ ", " - ").replace(" • ", " - "))
        _d, artist, title = parse_karaoke_filename(clean + ".x")
        if not artist and not title:
            artist, title = "", clean
        return {
            "source": source, "source_ref": vid,
            "artist": artist, "title": title,
            "confidence": 0.4, "needs_review": 1, "parse_method": "deterministic",
        }

    if source in (SOURCE_COMMUNITY, SOURCE_GEN):
        stem = os.path.splitext(name)[0]
        if stem.startswith("divebar__"):
            stem = stem[len("divebar__"):]
        _d, artist, title = parse_karaoke_filename(stem + ".x")
        if source == SOURCE_GEN:
            gm = _GEN_RE.search(name)
            ref = gm.group(1)[:8] if gm else _hash8(stem)
        else:
            brand = (_d or "DB").strip() or "DB"
            ref = f"{brand}-{_hash8(stem)}"
        return {
            "source": source, "source_ref": ref,
            "artist": artist, "title": strip_karaoke_noise(title),
            "confidence": 0.6, "needs_review": 1 if source == SOURCE_GEN else 0,
            "parse_method": "deterministic",
        }

    # upload / unknown — no natural key; caller supplies content hash as source_ref.
    stem = os.path.splitext(name)[0]
    clean = strip_karaoke_noise(stem)
    _d, artist, title = parse_karaoke_filename(clean + ".x")
    if not artist and not title:
        artist, title = "", clean
    return {
        "source": SOURCE_UPLOAD, "source_ref": None,
        "artist": artist, "title": title,
        "confidence": 0.3, "needs_review": 1, "parse_method": "deterministic",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_naming.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/naming.py kj-controller/tests/unit/test_naming.py
git commit -m "feat(naming): source classification, media_id derivation, deterministic parse"
```

---

### Task 3: `media_library.py` — the stable-ID SQLite store

**Files:**
- Create: `kj-controller/media_library.py`
- Test: `kj-controller/tests/unit/test_media_library.py`

**Interfaces:**
- Consumes: `text_normalize.normalize`.
- Produces (relied on by Tasks 4, 5): class `MediaLibraryStore(db_path)` with methods
  `upsert(record: dict) -> None`, `get(media_id) -> dict | None`,
  `get_by_path(file_path) -> dict | None`, `set_metadata(media_id, artist, title) -> bool`,
  `list_records(source=None, needs_review=None) -> list[dict]`, `delete(media_id) -> None`.
  `upsert` accepts keys: `media_id` (required), `source`, `source_ref`, `artist`, `title`,
  `confidence`, `parse_method`, `needs_review`, `raw_original_name`, `file_path`, `ext`.

- [ ] **Step 1: Write the failing tests**

```python
# kj-controller/tests/unit/test_media_library.py
from media_library import MediaLibraryStore


def _store():
    return MediaLibraryStore(":memory:")


def test_upsert_and_get_roundtrip():
    s = _store()
    s.upsert({
        "media_id": "yt-UM1XiyBmhM", "source": "youtube", "source_ref": "UM1XiyBmhM",
        "artist": "Bella Kay", "title": "iloveit", "confidence": 0.4,
        "parse_method": "deterministic", "needs_review": 1,
        "raw_original_name": "-UM1XiyBmhM__Sing King__Bella Kay - iloveit.mp4",
        "file_path": "/x/a.mp4", "ext": ".mp4",
    })
    row = s.get("yt-UM1XiyBmhM")
    assert row["artist"] == "Bella Kay"
    assert row["needs_review"] == 1
    assert row["artist_norm"]  # normalized field populated


def test_upsert_is_idempotent_on_media_id():
    s = _store()
    base = {"media_id": "nomad-0729", "source": "master", "artist": "Cher", "title": "Believe"}
    s.upsert(base)
    s.upsert({**base, "title": "Believe (v2)"})
    assert s.get("nomad-0729")["title"] == "Believe (v2)"
    assert len(s.list_records()) == 1


def test_get_by_path():
    s = _store()
    s.upsert({"media_id": "up-abc12345", "source": "upload", "file_path": "/y/b.mp4"})
    assert s.get_by_path("/y/b.mp4")["media_id"] == "up-abc12345"
    assert s.get_by_path("/nope.mp4") is None


def test_set_metadata_marks_manual_and_clears_review():
    s = _store()
    s.upsert({"media_id": "yt-x", "source": "youtube", "artist": "A", "title": "B",
              "needs_review": 1, "confidence": 0.4})
    assert s.set_metadata("yt-x", "Real Artist", "Real Title") is True
    row = s.get("yt-x")
    assert (row["artist"], row["title"]) == ("Real Artist", "Real Title")
    assert row["needs_review"] == 0
    assert row["parse_method"] == "manual"
    assert row["confidence"] is None


def test_list_filters():
    s = _store()
    s.upsert({"media_id": "a", "source": "youtube", "needs_review": 1})
    s.upsert({"media_id": "b", "source": "master", "needs_review": 0})
    assert {r["media_id"] for r in s.list_records(source="youtube")} == {"a"}
    assert {r["media_id"] for r in s.list_records(needs_review=1)} == {"a"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_media_library.py -v`
Expected: FAIL (`ModuleNotFoundError: media_library`).

- [ ] **Step 3: Implement `media_library.py`** (mirror `RotationStore`'s per-thread connection)

```python
# kj-controller/media_library.py
"""MediaLibraryStore: SQLite store of canonical media identity keyed by media_id.

Per-thread connections (threading.local) + WAL, mirroring RotationStore — a
shared connection across Flask + background threads caused a prior outage.
"""

import sqlite3
import threading

from text_normalize import normalize as _normalize


_COLUMNS = (
    "media_id", "source", "source_ref", "artist", "title", "artist_norm",
    "title_norm", "confidence", "parse_method", "needs_review",
    "raw_original_name", "file_path", "ext", "created_at", "updated_at",
)


class MediaLibraryStore:
    _MEMORY = ":memory:"

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._memory_conn = None
        self._memory_lock = threading.Lock()
        self.init_schema()

    def _open_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_conn(self):
        if self.db_path == self._MEMORY:
            if self._memory_conn is None:
                self._memory_conn = self._open_conn()
            return self._memory_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_conn()
            self._local.conn = conn
        return conn

    def _lock(self):
        return self._memory_lock if self.db_path == self._MEMORY else _NULLCTX

    def init_schema(self):
        conn = self._get_conn()
        with self._lock():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_library (
                    media_id          TEXT PRIMARY KEY,
                    source            TEXT NOT NULL DEFAULT '',
                    source_ref        TEXT,
                    artist            TEXT NOT NULL DEFAULT '',
                    title             TEXT NOT NULL DEFAULT '',
                    artist_norm       TEXT NOT NULL DEFAULT '',
                    title_norm        TEXT NOT NULL DEFAULT '',
                    confidence        REAL,
                    parse_method      TEXT,
                    needs_review      INTEGER NOT NULL DEFAULT 0,
                    raw_original_name TEXT,
                    file_path         TEXT,
                    ext               TEXT,
                    created_at        TEXT DEFAULT (datetime('now')),
                    updated_at        TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_source ON media_library(source)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_needs_review ON media_library(needs_review)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_path ON media_library(file_path)")
            conn.commit()

    def upsert(self, record):
        artist = record.get("artist", "") or ""
        title = record.get("title", "") or ""
        row = {
            "media_id": record["media_id"],
            "source": record.get("source", ""),
            "source_ref": record.get("source_ref"),
            "artist": artist,
            "title": title,
            "artist_norm": _normalize(artist),
            "title_norm": _normalize(title),
            "confidence": record.get("confidence"),
            "parse_method": record.get("parse_method"),
            "needs_review": int(record.get("needs_review", 0)),
            "raw_original_name": record.get("raw_original_name"),
            "file_path": record.get("file_path"),
            "ext": record.get("ext"),
        }
        conn = self._get_conn()
        with self._lock():
            conn.execute(
                """
                INSERT INTO media_library
                    (media_id, source, source_ref, artist, title, artist_norm, title_norm,
                     confidence, parse_method, needs_review, raw_original_name, file_path, ext)
                VALUES
                    (:media_id, :source, :source_ref, :artist, :title, :artist_norm, :title_norm,
                     :confidence, :parse_method, :needs_review, :raw_original_name, :file_path, :ext)
                ON CONFLICT(media_id) DO UPDATE SET
                    source=excluded.source, source_ref=excluded.source_ref,
                    artist=excluded.artist, title=excluded.title,
                    artist_norm=excluded.artist_norm, title_norm=excluded.title_norm,
                    confidence=excluded.confidence, parse_method=excluded.parse_method,
                    needs_review=excluded.needs_review,
                    raw_original_name=COALESCE(media_library.raw_original_name, excluded.raw_original_name),
                    file_path=excluded.file_path, ext=excluded.ext,
                    updated_at=datetime('now')
                """,
                row,
            )
            conn.commit()

    def get(self, media_id):
        cur = self._get_conn().execute(
            "SELECT * FROM media_library WHERE media_id=?", (media_id,)
        )
        r = cur.fetchone()
        return dict(r) if r else None

    def get_by_path(self, file_path):
        cur = self._get_conn().execute(
            "SELECT * FROM media_library WHERE file_path=?", (file_path,)
        )
        r = cur.fetchone()
        return dict(r) if r else None

    def set_metadata(self, media_id, artist, title):
        conn = self._get_conn()
        with self._lock():
            cur = conn.execute(
                """
                UPDATE media_library
                SET artist=?, title=?, artist_norm=?, title_norm=?,
                    parse_method='manual', confidence=NULL, needs_review=0,
                    updated_at=datetime('now')
                WHERE media_id=?
                """,
                (artist, title, _normalize(artist), _normalize(title), media_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_records(self, source=None, needs_review=None):
        clauses, params = [], []
        if source is not None:
            clauses.append("source=?")
            params.append(source)
        if needs_review is not None:
            clauses.append("needs_review=?")
            params.append(int(needs_review))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = self._get_conn().execute(
            f"SELECT * FROM media_library{where} ORDER BY updated_at DESC", params
        )
        return [dict(r) for r in cur.fetchall()]

    def delete(self, media_id):
        conn = self._get_conn()
        with self._lock():
            conn.execute("DELETE FROM media_library WHERE media_id=?", (media_id,))
            conn.commit()


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


_NULLCTX = _NullCtx()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_media_library.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/media_library.py kj-controller/tests/unit/test_media_library.py
git commit -m "feat(media_library): stable-media_id SQLite identity store"
```

---

### Task 4: Wire identity resolution into `MediaIndex.scan()`

**Files:**
- Modify: `kj-controller/media.py:89-148` (`MediaIndex.__init__` and the `scan()` per-file loop)
- Test: `kj-controller/tests/unit/test_media_library_scan.py`

**Interfaces:**
- Consumes: `naming.parse_identity`, `naming.extract_media_id`, `naming.media_id_for`,
  `naming.content_hash`, `MediaLibraryStore` (Task 3).
- Produces: `MediaIndex(config, media_library=None)`; after `scan()`, one `media_library` row per
  indexed media file, with `file_path` set and `media_id` resolved. New method
  `MediaIndex._resolve_media_id(real_path, fname) -> (media_id, identity_dict)`.

- [ ] **Step 1: Write the failing integration test**

```python
# kj-controller/tests/unit/test_media_library_scan.py
import os
from media import MediaIndex
from media_library import MediaLibraryStore


def _touch(path):
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 16)


def test_scan_populates_media_library(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    _touch(dl / "NOMAD-0729 - Cher - Believe.mp4")
    _touch(dl / "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4")
    idx_path = tmp_path / "media_index.json"

    store = MediaLibraryStore(":memory:")
    cfg = {
        "media_folders": [str(dl)],
        "download_folder": str(dl),
        "media_index_path": str(idx_path),
    }
    mi = MediaIndex(cfg, media_library=store)
    mi.scan()

    master = store.get("nomad-0729")
    assert master and master["artist"] == "Cher" and master["needs_review"] == 0
    yt = store.get("yt-UM1XiyBmhM")
    assert yt and yt["source"] == "youtube" and yt["needs_review"] == 1
    assert yt["file_path"].endswith(".mp4")


def test_scan_reuses_media_id_for_keyless_upload_without_rehash(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    _touch(dl / "Some Random Upload.mp4")
    store = MediaLibraryStore(":memory:")
    cfg = {"media_folders": [str(dl)], "download_folder": str(dl),
           "media_index_path": str(tmp_path / "i.json")}
    mi = MediaIndex(cfg, media_library=store)
    mi.scan()
    rows = store.list_records(source="upload")
    assert len(rows) == 1
    first_id = rows[0]["media_id"]
    assert first_id.startswith("up-")
    mi.scan()  # rescan must not create a duplicate row
    assert len(store.list_records(source="upload")) == 1
    assert store.get(first_id) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_media_library_scan.py -v`
Expected: FAIL (`MediaIndex.__init__` takes no `media_library`).

- [ ] **Step 3: Extend `MediaIndex`**

In `media.py`, update the constructor and imports:

```python
from naming import (
    parse_identity, extract_media_id, media_id_for, content_hash,
    SOURCE_UPLOAD,
)
```

```python
    def __init__(self, config, media_library=None):
        self.config = config
        self.index = {}
        self.media_library = media_library
```

Add a resolver method on the class:

```python
    def _resolve_media_id(self, real_path, fname):
        """Return (media_id, identity_dict) for a file.

        Prefers an embedded [media_id] slug token (post-migration, cheap); else
        derives from the filename pattern; for keyless uploads reuses an existing
        media_library row (by path) to avoid re-hashing on every scan.
        """
        identity = parse_identity(fname)
        token = extract_media_id(fname)
        if token:
            return token, identity
        if identity["source_ref"]:
            return media_id_for(identity["source"], identity["source_ref"]), identity
        # keyless upload: reuse a prior row's id if we already indexed this path
        if self.media_library is not None:
            existing = self.media_library.get_by_path(real_path)
            if existing:
                return existing["media_id"], identity
        return media_id_for(SOURCE_UPLOAD, content_hash(real_path)), identity
```

Inside `scan()`'s per-file loop, after the `entry = {...}` dict is built and the
`parse_youtube_filename` block runs (around `media.py:146`), add the upsert:

```python
                if self.media_library is not None:
                    try:
                        media_id, identity = self._resolve_media_id(real_path, fname)
                        entry["media_id"] = media_id
                        self.media_library.upsert({
                            "media_id": media_id,
                            "source": identity["source"],
                            "source_ref": identity["source_ref"],
                            "artist": identity["artist"],
                            "title": identity["title"],
                            "confidence": identity["confidence"],
                            "parse_method": identity["parse_method"],
                            "needs_review": identity["needs_review"],
                            "raw_original_name": fname,
                            "file_path": real_path,
                            "ext": ext,
                        })
                    except Exception as exc:  # never let indexing crash on one file
                        log_message(f"media_library upsert failed for {fname}: {exc}", self.config)
```

(No new skip needed for `_redundant_quarantine`: it is a *sibling* of the download folder, so
`os.walk` over `media_folders` never descends into it. `_playability_quarantine` and the preview
cache are already skipped by the existing loop.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_media_library_scan.py tests/unit/test_media.py -v`
Expected: PASS (new tests pass; existing media tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/media.py kj-controller/tests/unit/test_media_library_scan.py
git commit -m "feat(media): populate media_library on scan (media_id resolution)"
```

---

### Task 5: Instantiate the store in the app and pass it to `MediaIndex`

**Files:**
- Modify: `kj-controller/app.py:14` (imports), `app.py:200` and `app.py:388` (both `MediaIndex(cfg)` call sites)
- Test: `kj-controller/tests/unit/test_app_media_library_wired.py`

**Interfaces:**
- Consumes: `MediaLibraryStore` (Task 3), config key `media_db_path` (Task 1).
- Produces: `flask_app.media_library` attribute; both `MediaIndex(...)` calls pass `media_library=`.

- [ ] **Step 1: Write the failing test**

```python
# kj-controller/tests/unit/test_app_media_library_wired.py
import app as app_module


def test_app_factory_wires_media_library(tmp_path, monkeypatch):
    cfg = {
        "media_folders": [], "download_folder": str(tmp_path),
        "media_index_path": str(tmp_path / "i.json"),
        "media_db_path": str(tmp_path / "media_library.db"),
        "flask_port": 80, "websockify_enabled": False,
    }
    monkeypatch.setattr(app_module, "load_config", lambda *a, **k: cfg)
    flask_app = app_module.create_app()
    assert flask_app.media_library is not None
    assert flask_app.media.media_library is flask_app.media_library
```

(If `create_app()` has side effects that make this brittle, assert against the smallest slice that
proves wiring — e.g. call the constructor path directly. Keep the test focused on the wiring.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_app_media_library_wired.py -v`
Expected: FAIL (`flask_app` has no `media_library`).

- [ ] **Step 3: Wire it in `app.py`**

Add the import near the other store imports:

```python
from media_library import MediaLibraryStore
```

Before the first `MediaIndex(cfg)` (app.py:200), construct the store and attach it:

```python
    flask_app.media_library = MediaLibraryStore(cfg.get('media_db_path'))
    flask_app.media = MediaIndex(cfg, media_library=flask_app.media_library)
```

At the worker/second call site (app.py:388), pass the same-config store:

```python
    media = MediaIndex(cfg, media_library=MediaLibraryStore(cfg.get('media_db_path')))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_app_media_library_wired.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite (regression gate)**

Run: `cd kj-controller && pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add kj-controller/app.py kj-controller/tests/unit/test_app_media_library_wired.py
git commit -m "feat(app): construct MediaLibraryStore and wire into MediaIndex"
```

---

### Task 6: `sync_masters.py` — GCS rsync + `/rescan` poke

**Files:**
- Create: `kj-controller/scripts/sync_masters.py`
- Test: `kj-controller/tests/unit/test_sync_masters.py`

**Interfaces:**
- Consumes: config keys `master_sync_*` (Task 1), the local `/rescan` route (`routes.py:761`).
- Produces: `run_sync(config, *, gcloud_bin='gcloud', requests_lib=requests) -> dict`
  returning `{"changed": bool, "copied": int, "rescanned": bool, "error": str | None}`; a
  `main()` CLI entry that loads config and calls `run_sync`.

- [ ] **Step 1: Write the failing tests** (mock subprocess + requests)

```python
# kj-controller/tests/unit/test_sync_masters.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_sync_masters.py -v`
Expected: FAIL (`ModuleNotFoundError: scripts.sync_masters`).

- [ ] **Step 3: Implement `scripts/sync_masters.py`**

```python
# kj-controller/scripts/sync_masters.py
"""Sync the Nomad master catalog from GCS into the local NOMAD-720p mirror.

Additive `gcloud storage rsync` (never deletes local files), authed by a
read-only service-account key. On a run that copied anything, poke the local
/rescan so new masters index immediately. Designed for a 5-minute systemd timer;
failures are reported, never raised, so a flaky network can't wedge the timer.
"""

import os
import re
import subprocess
import sys

import requests

# Allow running as a module (systemd) or a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_config  # noqa: E402

_COPIED_RE = re.compile(r"^Copying ", re.MULTILINE)
LOCK_PATH = "/tmp/nomad-master-sync.lock"


def _dest(config):
    dest = config.get("master_sync_dest") or ""
    if dest:
        return dest
    return os.path.join(config.get("download_folder", ""), "NOMAD-720p")


def run_sync(config, *, gcloud_bin="gcloud", requests_lib=requests):
    if not config.get("master_sync_enabled"):
        return {"changed": False, "copied": 0, "rescanned": False, "error": "disabled"}

    src = config.get("master_sync_source", "")
    dest = _dest(config)
    key = config.get("master_sync_credentials_file", "")
    os.makedirs(dest, exist_ok=True)

    env = dict(os.environ)
    if key:
        # Use the SA key for THIS invocation only; never mutate global gcloud auth.
        env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = key

    cmd = [gcloud_bin, "storage", "rsync", "--recursive", src, dest]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    except Exception as exc:  # noqa: BLE001
        return {"changed": False, "copied": 0, "rescanned": False, "error": str(exc)}

    if proc.returncode != 0:
        return {"changed": False, "copied": 0, "rescanned": False,
                "error": (proc.stderr or "gcloud rsync failed").strip()[:500]}

    copied = len(_COPIED_RE.findall((proc.stdout or "") + (proc.stderr or "")))
    changed = copied > 0
    rescanned = False
    if changed and requests_lib is not None:
        try:
            port = config.get("flask_port", 80)
            requests_lib.post(f"http://localhost:{port}/rescan", timeout=30)
            rescanned = True
        except Exception:  # noqa: BLE001
            rescanned = False  # rescan will happen on the next natural scan anyway
    return {"changed": changed, "copied": copied, "rescanned": rescanned, "error": None}


def main():
    # flock so an overlapping (slow first-run) sync can't stack up.
    import fcntl
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another sync is running; skipping")
        return 0
    result = run_sync(load_config())
    print(result)
    return 0 if not result.get("error") or result["error"] == "disabled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Ensure `tests/` can import `scripts` as a package**

If `scripts/__init__.py` does not exist, create an empty one so `import scripts.sync_masters`
resolves:

```bash
test -f kj-controller/scripts/__init__.py || touch kj-controller/scripts/__init__.py
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_sync_masters.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/scripts/sync_masters.py kj-controller/scripts/__init__.py kj-controller/tests/unit/test_sync_masters.py
git commit -m "feat(sync): GCS master-catalog rsync + rescan poke script"
```

---

### Task 7: systemd units + operational runbook (root restructure, SA, install)

**Files:**
- Create: `kj-controller/deploy/nomad-master-sync.service`
- Create: `kj-controller/deploy/nomad-master-sync.timer`
- Create: `docs/MASTER-SYNC.md`

**Interfaces:**
- Consumes: `scripts/sync_masters.py` (Task 6); the read-only SA key on-device.
- Produces: installable systemd timer + a checklist runbook. (No app code; no unit test — verified
  live on-device, documented below.)

- [ ] **Step 1: Write the systemd service unit**

```ini
# kj-controller/deploy/nomad-master-sync.service
[Unit]
Description=Nomad master-catalog GCS sync (NOMAD-720p mirror)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=nomad
Nice=10
IOSchedulingClass=idle
# Adjust WorkingDirectory/paths to the deployed location on the device.
WorkingDirectory=/opt/nomad/kjbox/kj-controller
ExecStart=/usr/bin/python3 -m scripts.sync_masters
```

- [ ] **Step 2: Write the systemd timer unit**

```ini
# kj-controller/deploy/nomad-master-sync.timer
[Unit]
Description=Run Nomad master-catalog GCS sync every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Write the runbook `docs/MASTER-SYNC.md`**

Include, as an explicit checklist (all steps run by Andrew off-show):

```markdown
# Master-catalog GCS auto-sync — setup runbook

## One-time GCP setup (dedicated read-only SA)
1. Create SA `nomad-master-sync@nomadkaraoke.iam.gserviceaccount.com`.
2. Grant it `roles/storage.objectViewer` on bucket `nomadkaraoke-divebar-files` ONLY
   (bucket-scoped IAM binding, not project-wide).
3. Create a JSON key; copy to the device at `/opt/nomad/secrets/nomad-master-sync.json`
   (mode 600, owner nomad). Prefer managing the SA + binding via Pulumi if available.

## One-time device restructure (OFF-SHOW, after backups)
1. Back up: `cp media_index.json media_index.json.bak-<date>`,
   `cp rotation.db rotation.db.bak-<date>`.
2. Stop the service: `sudo systemctl stop kj-controller`.
3. Rename the download root:
   `sudo mv /opt/nomad/YTDownloads /opt/nomad/downloads`.
4. Move masters under it (seeds the mirror so rsync only pulls the ~104 new):
   `sudo mv /opt/nomad/MP4-720p /opt/nomad/downloads/NOMAD-720p`.
5. Update `config.json`:
   - `download_folder`: `/opt/nomad/downloads`
   - `media_folders`: `["/opt/nomad/downloads"]`  (NOMAD-720p is a child → auto-indexed)
   - `media_db_path`: `/opt/nomad/kjbox/kj-controller/media_library.db`
   - `master_sync_enabled`: `true`
   - `master_sync_credentials_file`: `/opt/nomad/secrets/nomad-master-sync.json`
6. Audit stale references to the old path: `grep -rn YTDownloads /opt/nomad/playability-run`,
   any systemd drop-ins, `preview_cache_dir` (leave empty → re-derives to
   `/opt/nomad/preview-cache`, still correct as a sibling of the new root).
7. Start the service: `sudo systemctl start kj-controller`; hit `/rescan` once to populate
   `media_library` for the existing library.

## Verify GCS auth BEFORE installing the timer
Run once as the nomad user:
`CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=/opt/nomad/secrets/nomad-master-sync.json \
  /opt/nomad/google-cloud-sdk/bin/gcloud storage ls "gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/" | head`
Expected: object listing (confirms the SA can read). If `gcloud` is not on PATH in the unit,
set an absolute `ExecStart` python that calls the full gcloud path, or add the SDK bin to the
service `Environment=PATH=...`.

## Install the timer
1. `sudo cp deploy/nomad-master-sync.{service,timer} /etc/systemd/system/`
2. `sudo systemctl daemon-reload`
3. First manual run (watch the ~104-file backfill): `sudo systemctl start nomad-master-sync.service`
   then `journalctl -u nomad-master-sync -f`.
4. Enable the timer: `sudo systemctl enable --now nomad-master-sync.timer`
5. Confirm cadence: `systemctl list-timers nomad-master-sync.timer`.

## Notes
- rsync is additive (no `--delete-unmatched-destination-objects`): a master removed from GCS is
  kept locally.
- First post-move run may re-pull a few masters if local mtimes differ; subsequent runs are tiny.
```

- [ ] **Step 4: Commit**

```bash
git add kj-controller/deploy/nomad-master-sync.service kj-controller/deploy/nomad-master-sync.timer docs/MASTER-SYNC.md
git commit -m "feat(deploy): master-sync systemd timer + setup runbook"
```

---

### Task 8: Full-suite regression + version bump

**Files:**
- Modify: `kj-controller/pyproject.toml` (version bump for the frontend cache-bust convention / release marker)

- [ ] **Step 1: Run the entire test suite**

Run: `cd kj-controller && pytest -q`
Expected: PASS (all green, including the four new test modules).

- [ ] **Step 2: Bump the version**

Bump the `version` in `kj-controller/pyproject.toml` (minor bump, e.g. `0.50.0 → 0.51.0`) per the
repo's per-PR version convention.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/pyproject.toml
git commit -m "chore: bump version for media-identity + master-sync (Phase 1)"
```

---

## Self-Review (against the spec)

- **Identity store** (spec §1): Task 3 `media_library` table matches the spec schema (incl.
  `*_norm`, `needs_review`, `raw_original_name`). ✓
- **`media_id` scheme** (spec §1 table): Task 2 `media_id_for` + `parse_identity` implement
  `yt-`/`db-`/`gen-`/`nomad-`/`up-`. Community `db-<brand>-<hash8>` fallback used (no file_id in a
  filename-only Phase-1 backfill; the real `db-<brand>-<fileid>` path is Phase 2's live-download
  work). ✓
- **On-disk slug** (spec §2): `build_slug_filename` provided (Task 2) but intentionally **not
  applied** to existing files in Phase 1 — renaming is Phase 4's reviewed migration. Constraint
  documented in Global Constraints. ✓
- **Scan integration / `media_id` recovery** (spec §6): Task 4 prefers the `[media_id]` token, then
  natural key, then reuse-by-path, then content hash. ✓
- **Master GCS sync** (spec §9): Tasks 6–7 — systemd timer, additive rsync, read-only SA via
  `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE`, `/rescan` poke, native `NOMAD-####` names preserved. ✓
- **Root restructure** (spec §2 / decisions): Task 7 runbook renames the root and moves masters. ✓
- **Offline resilience** (constraint): sync failures return an error dict, never raise; scan upsert
  is per-file try/except. ✓
- **Deferred to later phases (not in this plan, by design):** LLM parsing + gen endpoint,
  download-flow renaming + dedup-skip (P2); Available Songs edit/review UX + rotation `Artist -
  Title` flip (P3); reviewed backlog slug-migration (P4). Listed below.
- **Placeholder scan:** none — every code/test step has concrete content.
- **Type consistency:** `parse_identity` dict keys, `MediaLibraryStore` method names, and
  `run_sync` return shape are used consistently across Tasks 2→4→5→6.

## Later phases (separate plans)

- **P2 — Parsing pipeline + gen endpoint + download-flow renaming + dedup-skip:** add
  `POST /api/parse-karaoke-titles` to karaoke-gen; `GenClient.parse_titles`; a batch refine pass
  that upgrades `needs_review` rows; rename new downloads into `downloads/<source>/` with the slug;
  dedup-skip on existing `media_id`.
- **P3 — Available Songs edit/review UX + rotation flip:** surface canonical `Artist - Title`,
  editable fields + "Needs review" filter (built on the v0.50.0 `.rs-*` renderer); `POST
  /media/metadata`; flip rotation/KN/divebar builders to `Artist - Title`.
- **P4 — Reviewed backlog migration:** `scripts/normalize_download_library.py` (dry-run report →
  approve → `--execute`), moving/renaming the existing 1,087 downloads into source subfolders and
  repointing live rotation `file_path`s.
