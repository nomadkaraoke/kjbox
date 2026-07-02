# SSD Library Media Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SSD commercial-library files a stable content-derived `media_id` (`lib-<sha1[:12]>`) so play stats, canonical display, and backfill cover them — per `docs/archive/2026-07-02-ssd-library-media-identity-design.md` (rev 2, approved).

**Architecture:** Lazy materialization: `media_library` rows are created on touch (play/preview/link/import) by hashing file content, with artist/title from the external catalog. Hashing never runs on a request thread (daemon-thread seam) and never on the search hot path. A one-off script seeds rows for the ~400 SSD tracks referenced by rotation history so the existing play-stats backfill can attribute them unchanged.

**Tech Stack:** Python 3.11+ / Flask / SQLite (WAL, per-thread conns) / vanilla JS frontend. No new dependencies.

## Global Constraints

- Worktree: `/Users/andrew/Projects/nomadkaraoke/kjbox-naming-followup` (branch `feat/sess-20260702-1448-naming-followup`). NEVER edit the main clone `kjbox/`.
- All code paths below are relative to `kj-controller/` inside the worktree unless prefixed otherwise.
- Run tests as: `cd kj-controller && rtk proxy python -m pytest <path> -v > /tmp/t.txt` then READ `/tmp/t.txt`. Do NOT use `2>&1` or trailing `| tail` (the rtk shell hook mangles them). kjbox has NO pytest CI — local tests are the only gate.
- Stats/identity code must be fault-isolated: a failure may never break `/play`, search, or rotation endpoints (wrap in try/except, log, continue).
- Never clobber curated identity: an existing `media_library` row's artist/title/source/needs_review must survive re-touch (only `file_path`/`ext` refresh).
- Design doc for all rationale: `docs/archive/2026-07-02-ssd-library-media-identity-design.md`.
- Commit after every task (message style: `feat: …` / `test: …`, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`).

---

### Task 1: `library` source in naming.py

**Files:**
- Modify: `kj-controller/naming.py` (constants block at top + `media_id_for`)
- Test: `kj-controller/tests/unit/test_naming.py` (append)

**Interfaces:**
- Consumes: existing `media_id_for(source, source_ref)` prefix map.
- Produces: `naming.SOURCE_LIBRARY == "library"`; `media_id_for("library", "<hash>") -> "lib-<hash>"`. Later tasks import both.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_naming.py`:

```python
def test_library_source_media_id():
    # Design D1: SSD/library files get content-derived lib-<sha1[:12]> ids.
    from naming import media_id_for, SOURCE_LIBRARY, DOWNLOAD_SOURCES
    assert SOURCE_LIBRARY == "library"
    assert media_id_for(SOURCE_LIBRARY, "abc123def456") == "lib-abc123def456"
    # library rows must be outside scan's prune jurisdiction (PR #143 invariant)
    assert SOURCE_LIBRARY not in DOWNLOAD_SOURCES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/unit/test_naming.py::test_library_source_media_id -v > /tmp/t.txt` then read `/tmp/t.txt`
Expected: FAIL — `ImportError: cannot import name 'SOURCE_LIBRARY'`

- [ ] **Step 3: Implement** — in `naming.py`, add after `SOURCE_UPLOAD = "upload"`:

```python
SOURCE_LIBRARY = "library"  # external SSD library — files in place, content-hash ids
```

and in `media_id_for`, add to the prefix dict:

```python
        SOURCE_LIBRARY: "lib",
```

- [ ] **Step 4: Run test to verify it passes** (same command). Also run the whole file: `rtk proxy python -m pytest tests/unit/test_naming.py tests/unit/test_naming_merge.py -v > /tmp/t.txt`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/naming.py kj-controller/tests/unit/test_naming.py
git commit -m "feat: library media source (lib- content-hash ids)"
```

---

### Task 2: `ExternalCatalog.get_by_path` (NFC/NFD-tolerant)

**Files:**
- Modify: `kj-controller/catalog.py` (add `import unicodedata` to imports; new method on `ExternalCatalog`, place next to `search`)
- Test: `kj-controller/tests/unit/test_catalog_get_by_path.py` (create)

**Interfaces:**
- Consumes: existing `media` table (`path` UNIQUE, `filename`, `folder`, `disc_id`, `artist`, `title`, `format`), `is_available()`, `_get_conn()`.
- Produces: `catalog.get_by_path(path) -> dict | None` with keys `path, filename, folder, disc_id, artist, title, format`. Tasks 3/5/6 call it.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_catalog_get_by_path.py`:

```python
# kj-controller/tests/unit/test_catalog_get_by_path.py
import os
import unicodedata

from catalog import ExternalCatalog, parse_karaoke_filename


def _catalog_with(tmp_path, paths):
    cat = ExternalCatalog({"external_catalog_db": str(tmp_path / "cat.db")})
    cat.init_schema()
    conn = cat._get_conn()
    for p in paths:
        fname = os.path.basename(p)
        disc, artist, title = parse_karaoke_filename(fname)
        conn.execute(
            "INSERT INTO media (path, filename, folder, disc_id, artist, title, format) "
            "VALUES (?,?,?,?,?,?,?)",
            (p, fname, os.path.dirname(p), disc, artist, title, "zip"),
        )
    conn.commit()
    return cat


def test_get_by_path_exact(tmp_path):
    p = "/media/nomad/Nomad4TBOne/Discs/SC8123-04 - ABBA - SOS.zip"
    cat = _catalog_with(tmp_path, [p])
    row = cat.get_by_path(p)
    assert row and row["artist"] == "ABBA" and row["title"] == "SOS"


def test_get_by_path_nfc_nfd_variants(tmp_path):
    # Catalog built from an NFD (macOS-era) file list; runtime path is NFC.
    nfd = unicodedata.normalize("NFD", "/media/nomad/Nomad4TBOne/Discs/K1 - Céline Dion - Pour que tu m'aimes.zip")
    cat = _catalog_with(tmp_path, [nfd])
    nfc = unicodedata.normalize("NFC", nfd)
    assert nfc != nfd  # the test is vacuous if the path has no composing chars
    row = cat.get_by_path(nfc)
    assert row and "Dion" in row["artist"]


def test_get_by_path_miss_and_unavailable(tmp_path):
    cat = _catalog_with(tmp_path, [])
    assert cat.get_by_path("/media/nomad/Nomad4TBOne/nope.zip") is None
    empty = ExternalCatalog({"external_catalog_db": str(tmp_path / "absent.db")})
    assert empty.get_by_path("/media/nomad/x.zip") is None  # db missing -> None, no raise
    assert cat.get_by_path("") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `rtk proxy python -m pytest tests/unit/test_catalog_get_by_path.py -v > /tmp/t.txt` then read it.
Expected: FAIL — `AttributeError: 'ExternalCatalog' object has no attribute 'get_by_path'`
(Note: `test_get_by_path_miss_and_unavailable` — the empty-catalog case: `_catalog_with(tmp_path, [])` creates a schema'd but empty db, so `is_available()` is False there too; the miss assertion still exercises the None path.)

- [ ] **Step 3: Implement** — in `catalog.py`, add `import unicodedata` with the other imports, and add this method to `ExternalCatalog` (next to `search`):

```python
    def get_by_path(self, path):
        """Row for an exact file path, tolerant of NFC/NFD unicode variants.

        Rotation rows and the catalog were built from the same file list, but
        macOS-era lists can carry NFD while runtime paths are NFC (or vice
        versa) — try the raw path first, then both normal forms.
        """
        if not path or not self.is_available():
            return None
        conn = self._get_conn()
        variants = [path]
        for form in ("NFC", "NFD"):
            v = unicodedata.normalize(form, path)
            if v not in variants:
                variants.append(v)
        for v in variants:
            try:
                row = conn.execute(
                    "SELECT path, filename, folder, disc_id, artist, title, format "
                    "FROM media WHERE path = ?", (v,)
                ).fetchone()
            except sqlite3.OperationalError:
                return None
            if row:
                return dict(row)
        return None
```

- [ ] **Step 4: Run to verify pass** (same command) — all 3 PASS. Also `rtk proxy python -m pytest tests/unit/test_catalog.py -v > /tmp/t.txt` to confirm no regression.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/catalog.py kj-controller/tests/unit/test_catalog_get_by_path.py
git commit -m "feat: catalog by-path lookup, NFC/NFD tolerant"
```

---

### Task 3: `library_media.py` — identity materialization

**Files:**
- Create: `kj-controller/library_media.py`
- Test: `kj-controller/tests/unit/test_library_media.py` (create)

**Interfaces:**
- Consumes: `naming.content_hash(path) -> str` (full-file sha1[:12]), `naming.media_id_for`, `naming.SOURCE_LIBRARY`, `catalog.parse_karaoke_filename`, `catalog.get_by_path` (Task 2), `MediaLibraryStore.get / get_by_path / upsert / upsert_scanned`.
- Produces (used by Tasks 4/6):
  - `is_library_path(path, config) -> bool`
  - `ensure_library_row(path, catalog, media_library) -> dict | None`
  - `ensure_library_row_for_app(app, path) -> dict | None`
  - `run_async(target, *args) -> None` (daemon thread; tests monkeypatch to synchronous)

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_library_media.py`:

```python
# kj-controller/tests/unit/test_library_media.py
import os

import library_media
from media_library import MediaLibraryStore


class _FakeCatalog:
    def __init__(self, rows=None):
        self.rows = rows or {}
    def get_by_path(self, path):
        return self.rows.get(path)


def _touch(path, content=b"same-bytes"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def test_is_library_path():
    cfg = {"external_media_mount": "/media/nomad/Nomad4TBOne"}
    assert library_media.is_library_path("/media/nomad/Nomad4TBOne/Discs/x.zip", cfg)
    assert not library_media.is_library_path("/opt/nomad/downloads/x.mp4", cfg)
    assert not library_media.is_library_path("/media/nomad/Nomad4TBOne-evil/x.zip", cfg)
    assert not library_media.is_library_path("/media/nomad/Nomad4TBOne/x.zip", {})
    assert not library_media.is_library_path(None, cfg)


def test_ensure_creates_row_from_catalog(tmp_path):
    p = str(tmp_path / "Discs" / "SC1 - ABBA - SOS.zip")
    _touch(p)
    ml = MediaLibraryStore(":memory:")
    cat = _FakeCatalog({p: {"artist": "ABBA", "title": "SOS", "disc_id": "SC1"}})
    row = library_media.ensure_library_row(p, cat, ml)
    assert row["media_id"].startswith("lib-") and len(row["media_id"]) == len("lib-") + 12
    assert row["source"] == "library" and row["artist"] == "ABBA"
    assert row["parse_method"] == "catalog" and row["needs_review"] == 0
    assert row["file_path"] == p and row["ext"] == ".zip"


def test_ensure_catalog_miss_falls_back_to_deterministic(tmp_path):
    p = str(tmp_path / "Discs" / "XY9 - Queen - Under Pressure.zip")
    _touch(p)
    ml = MediaLibraryStore(":memory:")
    row = library_media.ensure_library_row(p, _FakeCatalog(), ml)
    assert row["artist"] == "Queen" and row["title"] == "Under Pressure"
    assert row["parse_method"] == "deterministic" and row["needs_review"] == 1


def test_ensure_existing_by_path_row_skips_hashing(tmp_path, monkeypatch):
    p = str(tmp_path / "Discs" / "SC1 - ABBA - SOS.zip")
    _touch(p)
    ml = MediaLibraryStore(":memory:")
    library_media.ensure_library_row(p, _FakeCatalog(), ml)
    def boom(_):
        raise AssertionError("content_hash must not run when a by-path row exists")
    monkeypatch.setattr(library_media, "content_hash", boom)
    row = library_media.ensure_library_row(p, _FakeCatalog(), ml)
    assert row is not None


def test_ensure_moved_file_same_id_heals_path_keeps_identity(tmp_path):
    old = str(tmp_path / "Discs" / "SC1 - ABBA - SOS.zip")
    _touch(old, b"identical-content")
    ml = MediaLibraryStore(":memory:")
    row1 = library_media.ensure_library_row(old, _FakeCatalog(), ml)
    ml.set_metadata(row1["media_id"], "ABBA", "S.O.S.")  # manual ✎ edit
    new = str(tmp_path / "Reorganised" / "ABBA — SOS (SC1).zip")
    _touch(new, b"identical-content")
    os.remove(old)
    row2 = library_media.ensure_library_row(new, _FakeCatalog(), ml)
    assert row2["media_id"] == row1["media_id"]          # same content -> same id
    assert row2["file_path"] == new                       # path healed
    assert row2["title"] == "S.O.S."                      # manual edit NOT clobbered
    assert row2["parse_method"] == "manual"


def test_ensure_missing_or_none_inputs(tmp_path):
    ml = MediaLibraryStore(":memory:")
    assert library_media.ensure_library_row(str(tmp_path / "gone.zip"), _FakeCatalog(), ml) is None
    assert library_media.ensure_library_row(None, _FakeCatalog(), ml) is None
    assert library_media.ensure_library_row("/x.zip", _FakeCatalog(), None) is None


def test_run_async_executes_target():
    import threading
    done = threading.Event()
    library_media.run_async(lambda: done.set())
    assert done.wait(2.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `rtk proxy python -m pytest tests/unit/test_library_media.py -v > /tmp/t.txt` then read it.
Expected: FAIL — `ModuleNotFoundError: No module named 'library_media'`

- [ ] **Step 3: Implement** — create `kj-controller/library_media.py`:

```python
# kj-controller/library_media.py
"""Canonical identity for external-library (SSD) files.

Design: docs/archive/2026-07-02-ssd-library-media-identity-design.md (rev 2).
Files stay in place on the SSD; identity is content-derived (lib-<sha1[:12]>)
so the KJ can rename/reorganise freely — after a move, the next touch
re-hashes to the same id and the row's file_path is refreshed. Rows
materialize lazily on touch; hashing never runs on a request thread (callers
use run_async) and never on the search hot path.
"""
import os
import threading

from catalog import parse_karaoke_filename
from naming import SOURCE_LIBRARY, content_hash, media_id_for


def is_library_path(path, config):
    """True when ``path`` lives under the configured external media mount."""
    mount = ((config or {}).get("external_media_mount") or "").rstrip("/")
    if not mount or not path:
        return False
    return path == mount or path.startswith(mount + "/")


def ensure_library_row(path, catalog, media_library):
    """Return the media_library row for an SSD file, creating it if needed.

    Fast path: an existing by-path row returns without touching the file.
    Otherwise hash the content; a row already existing under that id means
    the file moved — refresh file_path/ext only, preserving (possibly
    manually edited) identity. Brand-new ids get artist/title from the
    catalog, falling back to the deterministic filename parse with
    needs_review=1. Returns None when the file is absent/unreadable.
    """
    if media_library is None or not path:
        return None
    row = media_library.get_by_path(path)
    if row:
        return row
    if not os.path.isfile(path):
        return None
    try:
        digest = content_hash(path)
    except OSError:
        return None
    media_id = media_id_for(SOURCE_LIBRARY, digest)
    ext = os.path.splitext(path)[1].lower()
    if media_library.get(media_id) is not None:
        media_library.upsert_scanned(
            {"media_id": media_id, "file_path": path, "ext": ext})
        return media_library.get(media_id)
    basename = os.path.basename(path)
    try:
        cat_row = catalog.get_by_path(path) if catalog is not None else None
    except Exception:
        cat_row = None
    if cat_row and ((cat_row.get("artist") or "").strip()
                    or (cat_row.get("title") or "").strip()):
        artist = cat_row.get("artist") or ""
        title = cat_row.get("title") or ""
        parse_method, needs_review = "catalog", 0
    else:
        _disc, artist, title = parse_karaoke_filename(basename)
        parse_method, needs_review = "deterministic", 1
    media_library.upsert({
        "media_id": media_id,
        "source": SOURCE_LIBRARY,
        "source_ref": digest,
        "artist": artist,
        "title": title,
        "confidence": None,
        "parse_method": parse_method,
        "needs_review": needs_review,
        "raw_original_name": basename,
        "file_path": path,
        "ext": ext,
    })
    return media_library.get(media_id)


def ensure_library_row_for_app(app, path):
    """Thread-target convenience: resolve stores off the app object; never raises."""
    try:
        return ensure_library_row(
            path, getattr(app, "catalog", None), getattr(app, "media_library", None))
    except Exception:
        return None


def run_async(target, *args):
    """Run target(*args) on a daemon thread.

    Production never hashes multi-hundred-MB files on a request thread
    (design D3); tests monkeypatch this to run synchronously.
    """
    threading.Thread(target=target, args=args, daemon=True).start()
```

- [ ] **Step 4: Run to verify pass** — all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/library_media.py kj-controller/tests/unit/test_library_media.py
git commit -m "feat: library_media — lazy content-hash identity for SSD files"
```

---

### Task 4: Wire play/preview/link to background materialization

**Files:**
- Modify: `kj-controller/routes.py` — `_record_play_stat` (~line 75), `_record_preview_stat` (~line 114), `link_rotation_file` (~line 3079), plus two new module functions and one import
- Test: `kj-controller/tests/unit/test_routes_stats.py` (append), `kj-controller/tests/unit/test_link_gate.py` (append)

**Interfaces:**
- Consumes: `library_media.is_library_path / ensure_library_row / ensure_library_row_for_app / run_async` (Task 3); existing `_normalize_song_key`, `stats.record_play(media_id, *, entry_id, singer, artist, title, song_key)`, `stats.record_preview(media_id, *, artist, title, song_key)`.
- Produces: `routes._record_library_play(app, validated_path, entry_id)` and `routes._record_library_preview(app, file_path)` (thread targets). No route signatures change.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_routes_stats.py`:

```python
# --- SSD/library lazy materialization (design D3) ---
import routes as _routes_mod


class _FakeCatalogByPath:
    def __init__(self, rows):
        self.rows = rows
    def get_by_path(self, p):
        return self.rows.get(p)


def _library_setup(tmp_path, monkeypatch):
    """Real store + real file under a fake mount; run_async made synchronous."""
    from flask import current_app
    from media_library import MediaLibraryStore
    mount = str(tmp_path / "ssd")
    p = str(tmp_path / "ssd" / "Discs" / "SC1 - ABBA - SOS.zip")
    import os as _os
    _os.makedirs(_os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(b"zipbytes")
    current_app.kj_config = {"external_media_mount": mount}
    current_app.media_library = MediaLibraryStore(":memory:")
    current_app.catalog = _FakeCatalogByPath(
        {p: {"artist": "ABBA", "title": "SOS", "disc_id": "SC1"}})
    monkeypatch.setattr(_routes_mod.library_media, "run_async",
                        lambda target, *a: target(*a))
    return p


def test_record_play_stat_materializes_library_row(app_ctx, tmp_path, monkeypatch):
    from flask import current_app
    p = _library_setup(tmp_path, monkeypatch)
    current_app.stats = _FakeStats()
    current_app.rotation = _FakeRotation()
    routes._record_play_stat(p, 42)
    mid, kw = current_app.stats.plays[0]
    assert mid.startswith("lib-")
    assert kw["entry_id"] == 42 and kw["singer"] == "Celeste"
    assert kw["artist"] == "ABBA" and kw["title"] == "SOS"
    row = current_app.media_library.get_by_path(p)
    assert row and row["source"] == "library"


def test_record_play_stat_non_library_unresolved_still_noop(app_ctx, tmp_path, monkeypatch):
    from flask import current_app
    _library_setup(tmp_path, monkeypatch)
    current_app.stats = _FakeStats()
    current_app.rotation = None
    routes._record_play_stat("/opt/nomad/downloads/unknown.mp4", None)
    assert current_app.stats.plays == []


def test_record_preview_stat_materializes_library_row(app_ctx, tmp_path, monkeypatch):
    from flask import current_app
    p = _library_setup(tmp_path, monkeypatch)
    current_app.stats = _FakePreviewStats()
    routes._record_preview_stat({"source": "local", "file_path": p})
    mid, kw = current_app.stats.previews[0]
    assert mid.startswith("lib-") and kw["artist"] == "ABBA"


def test_resolve_row_media_id_never_hashes_library_paths(app_ctx, tmp_path, monkeypatch):
    """Search enrichment must stay pure — an untouched SSD row resolves to None."""
    from flask import current_app
    p = _library_setup(tmp_path, monkeypatch)
    def boom(_):
        raise AssertionError("search enrichment must not hash")
    monkeypatch.setattr(_routes_mod.library_media, "content_hash", boom, raising=False)
    import library_media as _lm
    monkeypatch.setattr(_lm, "content_hash", boom)
    assert routes.resolve_row_media_id({"path": p}, "local", current_app.media_library) is None
    assert current_app.media_library.get_by_path(p) is None
```

and append to `tests/unit/test_link_gate.py` (inside `TestLinkPlayabilityGate`, reusing its imports/fixtures):

```python
    def test_link_materializes_library_row_async(self, gate_client, mock_rotation):
        """Linking an SSD path schedules background identity materialization."""
        good_result = types.SimpleNamespace(verdict={"overall_ok": True, "reasons": []})
        gate_client.application.kj_config["external_media_mount"] = "/media/nomad/Nomad4TBOne"
        with patch("routes._playability_gate", return_value=good_result), \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)), \
             patch("routes._enqueue_tier2"), \
             patch("routes.library_media.run_async") as run_async:
            resp = gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1,
                                 "file_path": "/media/nomad/Nomad4TBOne/Discs/a.zip"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        args = run_async.call_args[0]
        assert args[0] is routes.library_media.ensure_library_row_for_app
        assert args[2] == "/media/nomad/Nomad4TBOne/Discs/a.zip"

    def test_link_non_library_path_no_materialization(self, gate_client, mock_rotation):
        good_result = types.SimpleNamespace(verdict={"overall_ok": True, "reasons": []})
        gate_client.application.kj_config["external_media_mount"] = "/media/nomad/Nomad4TBOne"
        with patch("routes._playability_gate", return_value=good_result), \
             patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None)), \
             patch("routes._enqueue_tier2"), \
             patch("routes.library_media.run_async") as run_async:
            gate_client.post(
                "/rotation/link",
                data=json.dumps({"id": 1, "file_path": "/opt/nomad/downloads/a.mp4"}),
                content_type="application/json",
            )
        run_async.assert_not_called()
```

(If `gate_client.application.kj_config` is not a plain dict in that fixture, set it with `gate_client.application.kj_config = {"external_media_mount": "/media/nomad/Nomad4TBOne"}` instead — check the fixture at the top of `test_link_gate.py` first.)

- [ ] **Step 2: Run to verify failure**

Run: `rtk proxy python -m pytest tests/unit/test_routes_stats.py tests/unit/test_link_gate.py -v > /tmp/t.txt` then read it.
Expected: new tests FAIL — `AttributeError: module 'routes' has no attribute 'library_media'` (and plays list empty). Pre-existing tests still PASS.

- [ ] **Step 3: Implement** — in `routes.py`:

(a) Add to the module imports (near the other local imports at the top):

```python
import library_media
```

(b) In `_record_play_stat`, insert between the `extract_media_id` fallback and the existing `if not media_id: return`:

```python
        if not media_id and library_media.is_library_path(
                validated_path, getattr(current_app, 'kj_config', None)):
            # SSD/library file with no row yet: hash + materialize off-thread —
            # a cold multi-hundred-MB MP4 must never stall /play (design D3).
            library_media.run_async(
                _record_library_play, current_app._get_current_object(),
                validated_path, entry_id)
            return
```

(c) In `_record_preview_stat`, inside the `if source == 'local' and ml:` branch, after the existing row lookup lines, add:

```python
            if not media_id and library_media.is_library_path(
                    descriptor.get('file_path'),
                    getattr(current_app, 'kj_config', None)):
                library_media.run_async(
                    _record_library_preview, current_app._get_current_object(),
                    descriptor.get('file_path'))
                return
```

(d) Add the two thread targets as module functions directly below `_record_preview_stat`:

```python
def _record_library_play(app, validated_path, entry_id):
    """Thread target: materialize an SSD file's identity row, then record the play.

    Runs off the request thread (hashing can take seconds cold). The
    per-rotation-entry dedup index in StatsStore keeps a delayed insert
    idempotent. Never raises.
    """
    try:
        row = library_media.ensure_library_row_for_app(app, validated_path)
        if not row:
            return
        singer = None
        rotation = getattr(app, 'rotation', None)
        if entry_id and rotation:
            entry = rotation.store.get_entry(entry_id)
            if entry:
                singer = entry.get('singer')
        song_key = _normalize_song_key(row.get('artist'), row.get('title'))
        app.stats.record_play(row['media_id'], entry_id=entry_id, singer=singer,
                              artist=row.get('artist'), title=row.get('title'),
                              song_key=song_key)
    except Exception as e:
        try:
            log_message(f"stats: library play record failed: {e}", app.kj_config)
        except Exception:
            pass


def _record_library_preview(app, file_path):
    """Thread target: materialize an SSD file's identity row, then record the preview."""
    try:
        row = library_media.ensure_library_row_for_app(app, file_path)
        if not row:
            return
        song_key = _normalize_song_key(row.get('artist'), row.get('title'))
        app.stats.record_preview(row['media_id'], artist=row.get('artist'),
                                 title=row.get('title'), song_key=song_key)
    except Exception as e:
        try:
            log_message(f"stats: library preview record failed: {e}", app.kj_config)
        except Exception:
            pass
```

(e) In `link_rotation_file`, immediately after `rotation.link_file(entry_id, file_path)`:

```python
        if library_media.is_library_path(file_path, current_app.kj_config):
            # Materialize the identity row now so canonical display + the note
            # editor work before the first play (design D3.3).
            library_media.run_async(
                library_media.ensure_library_row_for_app,
                current_app._get_current_object(), file_path)
```

- [ ] **Step 4: Run to verify pass**

Run: `rtk proxy python -m pytest tests/unit/test_routes_stats.py tests/unit/test_link_gate.py -v > /tmp/t.txt` — all PASS (old + new).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_routes_stats.py kj-controller/tests/unit/test_link_gate.py
git commit -m "feat: play/preview/link materialize SSD library identity off-thread"
```

---

### Task 5: Rotation `media_meta` display enrichment (design D4)

**Files:**
- Modify: `kj-controller/routes.py` — `_decorate_rotation_entries` (~line 2757) + new `_add_media_meta`
- Modify: `kj-controller/static/app.js` — the file-path row block (~line 4444, `if (entry.file_path) {`)
- Test: `kj-controller/tests/unit/test_media_meta.py` (create)

**Interfaces:**
- Consumes: `media_library.get_by_path`, `catalog.get_by_path` (Task 2).
- Produces: each linked rotation entry gains `media_meta = {"artist": str, "title": str}` when identity is known (key absent otherwise). Frontend renders `Artist - Title · basename` in the toggleable file-path row.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_media_meta.py`:

```python
# kj-controller/tests/unit/test_media_meta.py
"""_add_media_meta: canonical Artist/Title decoration for linked rotation
entries — media_library first, external catalog fallback, never raises."""
import routes


class _ML:
    def __init__(self, rows):
        self.rows = rows
    def get_by_path(self, p):
        return self.rows.get(p)


class _Cat:
    def __init__(self, rows):
        self.rows = rows
    def get_by_path(self, p):
        return self.rows.get(p)


def test_media_meta_from_media_library(app_ctx):
    from flask import current_app
    current_app.media_library = _ML(
        {"/opt/nomad/downloads/youtube/x.mp4": {"artist": "ABBA", "title": "SOS"}})
    current_app.catalog = _Cat({})
    entries = [{"id": 1, "file_path": "/opt/nomad/downloads/youtube/x.mp4"},
               {"id": 2}]  # unlinked
    routes._add_media_meta(entries)
    assert entries[0]["media_meta"] == {"artist": "ABBA", "title": "SOS"}
    assert "media_meta" not in entries[1]


def test_media_meta_catalog_fallback_for_untouched_ssd(app_ctx):
    from flask import current_app
    p = "/media/nomad/Nomad4TBOne/Discs/SC1 - ABBA - SOS.zip"
    current_app.media_library = _ML({})
    current_app.catalog = _Cat({p: {"artist": "ABBA", "title": "SOS"}})
    entries = [{"id": 1, "file_path": p}]
    routes._add_media_meta(entries)
    assert entries[0]["media_meta"] == {"artist": "ABBA", "title": "SOS"}


def test_media_meta_blank_identity_and_errors_skipped(app_ctx):
    from flask import current_app

    class _Boom:
        def get_by_path(self, p):
            raise RuntimeError("db down")

    current_app.media_library = _ML({"/a.mp4": {"artist": "", "title": ""}})
    current_app.catalog = _Cat({})
    entries = [{"id": 1, "file_path": "/a.mp4"}]
    routes._add_media_meta(entries)
    assert "media_meta" not in entries[0]  # blank identity is not decoration
    current_app.media_library = _Boom()
    routes._add_media_meta(entries)       # must not raise


def test_decorate_rotation_entries_includes_media_meta(app_ctx, monkeypatch):
    from flask import current_app
    current_app.media_library = _ML({"/a.mp4": {"artist": "A", "title": "T"}})
    current_app.catalog = _Cat({})
    for name in ("_add_time_estimates", "_add_songs_sung", "_add_last_sang", "_add_sms_status"):
        monkeypatch.setattr(routes, name, lambda *a, **k: None)
    entries = [{"id": 1, "file_path": "/a.mp4"}]
    routes._decorate_rotation_entries(entries, rotation=None)
    assert entries[0]["media_meta"]["artist"] == "A"
```

- [ ] **Step 2: Run to verify failure**

Run: `rtk proxy python -m pytest tests/unit/test_media_meta.py -v > /tmp/t.txt`
Expected: FAIL — `AttributeError: module 'routes' has no attribute '_add_media_meta'`

- [ ] **Step 3: Implement backend** — in `routes.py`, add above `_decorate_rotation_entries`:

```python
def _add_media_meta(entries):
    """Attach canonical ``media_meta = {artist, title}`` to linked entries.

    media_library first (covers downloads + touched SSD rows, incl. manual ✎
    edits), external catalog fallback (untouched SSD files). Display-only and
    best-effort — a store/catalog error just leaves the key absent.
    """
    ml = getattr(current_app, 'media_library', None)
    catalog = getattr(current_app, 'catalog', None)
    for e in entries:
        fp = e.get('file_path')
        if not fp:
            continue
        try:
            row = ml.get_by_path(fp) if ml else None
            if not row and catalog is not None:
                row = catalog.get_by_path(fp)
            if row and ((row.get('artist') or '').strip()
                        or (row.get('title') or '').strip()):
                e['media_meta'] = {'artist': row.get('artist') or '',
                                   'title': row.get('title') or ''}
        except Exception:
            continue
```

and add `_add_media_meta(entries)` as the last line of `_decorate_rotation_entries` (after `_add_sms_status(entries)`).

- [ ] **Step 4: Run to verify pass** — all 4 PASS. Also run `rtk proxy python -m pytest tests/unit tests/integration -q > /tmp/t.txt` (decorator runs in many endpoint tests — confirm no regression).

- [ ] **Step 5: Implement frontend** — in `static/app.js`, replace the body of the `if (entry.file_path) {` block in the file-path row (~line 4444):

```javascript
        if (entry.file_path) {
            const parts = entry.file_path.split('/');
            const basename = parts[parts.length - 1];
            const mm = entry.media_meta;
            const canonical = mm && (mm.artist || mm.title)
                ? [mm.artist, mm.title].filter(Boolean).join(' - ') : null;
            // Canonical identity first (SSD/commercial files often have opaque
            // disc filenames); keep the real basename so the KJ can still spot
            // files that need unlinking + re-linking with a better version.
            pathRow.textContent = canonical ? `${canonical} · ${basename}` : basename;
            pathRow.title = entry.file_path;
        } else {
```

Verify JS syntax: `node --check kj-controller/static/app.js` → no output.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/static/app.js kj-controller/tests/unit/test_media_meta.py
git commit -m "feat: rotation entries carry canonical media_meta; file-path row shows it"
```

---

### Task 6: `scripts/import_rotation_ssd_tracks.py` (one-off seeder)

**Files:**
- Create: `kj-controller/scripts/import_rotation_ssd_tracks.py`
- Test: `kj-controller/tests/unit/test_import_rotation_ssd_tracks.py` (create)

**Interfaces:**
- Consumes: `library_media.ensure_library_row` (Task 3), `ExternalCatalog` (+ Task 2 `get_by_path`), `MediaLibraryStore`, rotation DB tables `rotation_entries` / `rotation_archive` (column `file_path`).
- Produces: CLI `python -m scripts.import_rotation_ssd_tracks --rotation-db … --media-db … --catalog-db … --mount … [--execute]`; module function `run(rotation_db, media_db, catalog_db, mount, execute=False) -> (counts, missing, catalog_misses)`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_import_rotation_ssd_tracks.py`:

```python
# kj-controller/tests/unit/test_import_rotation_ssd_tracks.py
import os
import sqlite3

from catalog import ExternalCatalog, parse_karaoke_filename
from media_library import MediaLibraryStore
from scripts.import_rotation_ssd_tracks import rotation_ssd_paths, run


def _rotation_db(tmp_path, active_paths, archive_paths):
    db = str(tmp_path / "rotation.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE rotation_entries (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.execute("CREATE TABLE rotation_archive (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.executemany("INSERT INTO rotation_entries (file_path) VALUES (?)",
                     [(p,) for p in active_paths])
    conn.executemany("INSERT INTO rotation_archive (file_path) VALUES (?)",
                     [(p,) for p in archive_paths])
    conn.commit()
    conn.close()
    return db


def _catalog_db(tmp_path, paths):
    db = str(tmp_path / "cat.db")
    cat = ExternalCatalog({"external_catalog_db": db})
    cat.init_schema()
    conn = cat._get_conn()
    for p in paths:
        fname = os.path.basename(p)
        disc, artist, title = parse_karaoke_filename(fname)
        conn.execute(
            "INSERT INTO media (path, filename, folder, disc_id, artist, title, format) "
            "VALUES (?,?,?,?,?,?,?)",
            (p, fname, os.path.dirname(p), disc, artist, title, "zip"))
    conn.commit()
    return db


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(path.encode())  # unique content per file -> unique lib- ids


def test_rotation_ssd_paths_unions_and_filters(tmp_path):
    mount = str(tmp_path / "ssd")
    a = f"{mount}/Discs/A.zip"
    b = f"{mount}/Discs/B.zip"
    db = _rotation_db(tmp_path, [a, "/opt/nomad/downloads/x.mp4", None],
                      [b, a])  # duplicate across tables + non-SSD + NULL
    assert rotation_ssd_paths(db, mount) == sorted([a, b])


def test_dry_run_reports_without_writing_or_hashing(tmp_path):
    mount = str(tmp_path / "ssd")
    a = f"{mount}/Discs/SC1 - ABBA - SOS.zip"
    gone = f"{mount}/Discs/GONE.zip"
    _touch(a)
    rot = _rotation_db(tmp_path, [a], [gone])
    cat = _catalog_db(tmp_path, [a])
    media_db = str(tmp_path / "ml.db")
    counts, missing, _ = run(rot, media_db, cat, mount, execute=False)
    assert counts["imported"] == 1 and counts["missing"] == 1
    assert missing == [gone]
    assert MediaLibraryStore(media_db).list_records(source="library") == []


def test_execute_imports_idempotently(tmp_path):
    mount = str(tmp_path / "ssd")
    a = f"{mount}/Discs/SC1 - ABBA - SOS.zip"        # in catalog
    b = f"{mount}/Loose/weird~name.zip"               # NOT in catalog
    _touch(a); _touch(b)
    rot = _rotation_db(tmp_path, [a], [b])
    cat = _catalog_db(tmp_path, [a])
    media_db = str(tmp_path / "ml.db")

    counts, _, catalog_misses = run(rot, media_db, cat, mount, execute=True)
    assert counts["imported"] == 2 and counts["already"] == 0
    assert catalog_misses == [b]
    ml = MediaLibraryStore(media_db)
    row_a = ml.get_by_path(a)
    assert row_a["media_id"].startswith("lib-") and row_a["artist"] == "ABBA"
    assert row_a["needs_review"] == 0 and row_a["parse_method"] == "catalog"
    row_b = ml.get_by_path(b)
    assert row_b["needs_review"] == 1 and row_b["parse_method"] == "deterministic"

    counts2, _, _ = run(rot, media_db, cat, mount, execute=True)
    assert counts2["already"] == 2 and counts2["imported"] == 0

    # The unchanged play-stats backfill can now resolve these paths.
    from scripts.backfill_play_stats import _resolve_media_id
    mid, artist, _title = _resolve_media_id(ml, a)
    assert mid == row_a["media_id"] and artist == "ABBA"
```

- [ ] **Step 2: Run to verify failure**

Run: `rtk proxy python -m pytest tests/unit/test_import_rotation_ssd_tracks.py -v > /tmp/t.txt`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.import_rotation_ssd_tracks'`

- [ ] **Step 3: Implement** — create `kj-controller/scripts/import_rotation_ssd_tracks.py`:

```python
"""One-off: seed media_library rows for SSD tracks referenced by rotation history.

Design D2 (docs/archive/2026-07-02-ssd-library-media-identity-design.md):
rotation-scoped import (~400 files, ~2.9 GB of hashing — minutes), NOT the
whole 415K-row catalog. Dry-run by default (no hashing, no writes). Run
on-device off-show AFTER relocating media_db_path (design D5), BEFORE the
play-stats backfill:

  /opt/nomad/kjbox/kj-controller/venv/bin/python -m scripts.import_rotation_ssd_tracks \
      --rotation-db /home/nomad/kjdata/rotation.db \
      --media-db /opt/nomad/data/media_library.db \
      --catalog-db /opt/nomad/kjbox/kj-controller/external_media.db \
      --mount /media/nomad/Nomad4TBOne          # add --execute after review
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from catalog import ExternalCatalog  # noqa: E402
from library_media import ensure_library_row  # noqa: E402
from media_library import MediaLibraryStore  # noqa: E402


def rotation_ssd_paths(rotation_db, mount):
    """Distinct SSD file_paths across active + archive rotation tables."""
    conn = sqlite3.connect(f"file:{rotation_db}?mode=ro", uri=True)
    like = mount.rstrip("/") + "/%"
    paths = set()
    for table in ("rotation_entries", "rotation_archive"):
        try:
            rows = conn.execute(
                f"SELECT DISTINCT file_path FROM {table} WHERE file_path LIKE ?",
                (like,)).fetchall()
        except sqlite3.OperationalError:
            continue  # table absent in older DBs
        paths.update(fp for (fp,) in rows if fp)
    conn.close()
    return sorted(paths)


def run(rotation_db, media_db, catalog_db, mount, execute=False):
    ml = MediaLibraryStore(media_db)
    catalog = ExternalCatalog({"external_catalog_db": catalog_db})
    counts = {"already": 0, "imported": 0, "catalog_miss": 0,
              "missing": 0, "failed": 0}
    missing, catalog_misses = [], []
    for path in rotation_ssd_paths(rotation_db, mount):
        if ml.get_by_path(path):
            counts["already"] += 1
            continue
        if not os.path.isfile(path):
            counts["missing"] += 1
            missing.append(path)
            continue
        if catalog.get_by_path(path) is None:
            # counted within 'imported' too — these land with a deterministic
            # filename parse + needs_review=1 (fix later via the ✎ editor)
            counts["catalog_miss"] += 1
            catalog_misses.append(path)
        if not execute:
            counts["imported"] += 1  # would import
            continue
        row = ensure_library_row(path, catalog, ml)
        if row:
            counts["imported"] += 1
        else:
            counts["failed"] += 1
    return counts, missing, catalog_misses


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rotation-db", required=True)
    ap.add_argument("--media-db", required=True)
    ap.add_argument("--catalog-db", required=True)
    ap.add_argument("--mount", required=True)
    ap.add_argument("--execute", action="store_true",
                    help="hash + write rows (default: dry-run report only)")
    args = ap.parse_args()
    counts, missing, catalog_misses = run(
        args.rotation_db, args.media_db, args.catalog_db, args.mount,
        execute=args.execute)
    mode = "EXECUTED" if args.execute else "DRY-RUN (no writes, no hashing)"
    print(f"{mode}: {counts}")
    for p in missing:
        print(f"  MISSING (skipped): {p}")
    for p in catalog_misses:
        print(f"  CATALOG MISS (deterministic parse, needs_review=1): {p}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass** — all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/scripts/import_rotation_ssd_tracks.py kj-controller/tests/unit/test_import_rotation_ssd_tracks.py
git commit -m "feat: rotation-scoped SSD library import script (dry-run default)"
```

---

### Task 7: Finalize — version bump, full suite, review, PR

**Files:**
- Modify: `kj-controller/pyproject.toml` (`version = "0.57.0"` → `"0.58.0"` — new backend feature + app.js change, minor bump; re-check current value first, parallel sessions bump it)

**Interfaces:** none new.

- [ ] **Step 1: Bump version** in `kj-controller/pyproject.toml` (frontend cache-bust `app.js?v=` reads it at startup).

- [ ] **Step 2: Full suite**

Run: `rtk proxy python -m pytest tests/unit tests/integration -q > /tmp/t.txt` then read it.
Expected: all pass (1 known environmental skip: Xvfb/cvlc). Also `node --check kj-controller/static/app.js`.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/pyproject.toml
git commit -m "chore: bump version to 0.58.0"
```

- [ ] **Step 4: CodeRabbit review**

Run: `coderabbit review --agent --type committed --base origin/main > /tmp/cr.txt` (NO `2>&1`). Fix real findings (max 3 cycles); if rate-limited, fall back to the `feature-dev:code-reviewer` agent on the branch diff.

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feat/sess-20260702-1448-naming-followup
gh pr create --title "feat: SSD commercial library — content-hash media identity + play stats (v0.58.0)" --body "…summary; include '@coderabbitai ignore' line and the Claude Code footer…"
```

PR body must note: backend change → off-show restart; device follow-ups in order = D5 relocation runbook (design doc) → `import_rotation_ssd_tracks` dry-run → Andrew reviews → `--execute` → play-stats backfill re-run → verify stats UI.

---

## Device rollout (after merge — operator steps, not code)

Per design D5/Rollout, off-show, with Andrew:
1. Relocation runbook (stop service → WAL-checkpoint → copy `media_library.db` to `/opt/nomad/data/` → `media_db_path` in config.json → start → verify counts).
2. `import_rotation_ssd_tracks` dry-run → review → `--execute`.
3. `backfill_play_stats.py` dry-run → review → `--execute` (unchanged script; rows now resolve).
4. Verify: leaderboards include commercial-disc plays; SSD search rows show badges after touch; rotation file-path rows show canonical names.
