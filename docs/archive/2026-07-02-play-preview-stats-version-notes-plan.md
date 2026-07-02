# Play/Preview Stats + Version Notes + Singer Leaderboards — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record play/preview counts and version notes per `media_id`, backfill ~880 historic plays, surface counts + notes on rotation-search rows, and add a singer/song leaderboard view.

**Architecture:** A new `StatsStore` (sibling of `MediaLibraryStore`, sharing `media_library.db`) holds append-only `play_events`/`preview_events` and a `version_notes` table, all keyed by `media_id` (never wiped nightly). Routes record on `/play` and `/preview/resolve`, enrich `/rotation/search` rows, expose notes + leaderboard endpoints. All recording/reads are fault-isolated so a stats failure never affects a live show.

**Tech Stack:** Python 3 + Flask, SQLite (WAL, per-thread connections), vanilla JS frontend, pytest.

**Design spec:** `docs/archive/2026-07-01-play-preview-stats-version-notes-design.md` (authoritative).

## Global Constraints

- **Storage:** new tables live in the **same DB file as `media_library`** (`cfg.get('media_db_path')`), NOT `rotation.db`. Keyed by `media_id` (plain TEXT, no FK).
- **Identity keys:** per-version = `media_id`; logical song = `text_normalize.group_key(artist, title)` (via `routes._normalize_song_key`); singer = `" ".join(singer.split()).lower()`.
- **Connection idiom:** copy `MediaLibraryStore` exactly — per-thread `sqlite3` via `threading.local()`, `PRAGMA journal_mode=WAL`, `busy_timeout=10000`, `synchronous=NORMAL`, a `:memory:` shared-connection special case, and a `_NULLCTX`/`_memory_lock` guard. **Never nest `with self._lock()` calls** (the memory lock is a non-reentrant `threading.Lock`).
- **Fault isolation:** every stats read/write invoked from a route is wrapped in `try/except`, logged via `log_message(..., current_app.kj_config)`, and swallowed. Core play/preview/search behavior must succeed regardless.
- **No network/LLM/GCS** in any stats code — pure local SQLite.
- **Device deploy:** backend changes need an off-show restart; frontend needs a `pyproject` version bump for `app.js?v=` cache-bust. Both flagged, not executed by this plan.
- **Phase coordination:** `/rotation/search` enrichment + `static/app.js` row rendering (Task 13) overlap the naming initiative's Phase 3 — build them as a self-contained additive layer. Tasks 1–12 are P2/P3-independent.

---

### Task 1: `StatsStore` module — schema + connection idiom

**Files:**
- Create: `kj-controller/stats_store.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces: `StatsStore(db_path)` with `init_schema()` run in `__init__`; module-level `_norm_singer(s) -> str`. Tables `play_events`, `preview_events`, `version_notes` per spec §4.1.

- [ ] **Step 1: Write the failing test**

```python
# kj-controller/tests/unit/test_stats_store.py
import pytest
from stats_store import StatsStore, _norm_singer


@pytest.fixture
def store():
    return StatsStore(":memory:")


def _tables(store):
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_tables(store):
    assert {"play_events", "preview_events", "version_notes"} <= _tables(store)


def test_schema_idempotent_on_file(tmp_path):
    db = str(tmp_path / "media_library.db")
    StatsStore(db)
    s2 = StatsStore(db)  # second open must not raise
    assert {"play_events", "preview_events", "version_notes"} <= _tables(s2)


def test_norm_singer():
    assert _norm_singer("  Celeste   B ") == "celeste b"
    assert _norm_singer(None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stats_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# kj-controller/stats_store.py
"""StatsStore: per-version play/preview counts + version notes, keyed by media_id.

Lives in the SAME SQLite file as media_library (media_db_path) — library-wide,
never wiped nightly. Per-thread connections + WAL, mirroring MediaLibraryStore.
"""

import sqlite3
import threading


def _norm_singer(s):
    """Collapse whitespace + lowercase for singer grouping (names not unique)."""
    return " ".join((s or "").split()).lower()


class StatsStore:
    _MEMORY = ":memory:"

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._memory_conn = None
        self._memory_lock = threading.Lock()
        self.init_schema()

    def _open_conn(self):
        conn = sqlite3.connect(
            self.db_path, timeout=10,
            check_same_thread=(self.db_path != self._MEMORY))
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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS play_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id    TEXT NOT NULL,
                    song_key    TEXT,
                    singer      TEXT,
                    singer_norm TEXT,
                    played_at   TEXT NOT NULL,
                    night_date  TEXT,
                    entry_id    INTEGER,
                    source      TEXT NOT NULL DEFAULT 'live',
                    artist      TEXT,
                    title       TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_play_events_media  ON play_events(media_id);
                CREATE INDEX IF NOT EXISTS idx_play_events_song   ON play_events(song_key);
                CREATE INDEX IF NOT EXISTS idx_play_events_singer ON play_events(singer_norm);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_play_events_entry
                    ON play_events(entry_id) WHERE entry_id IS NOT NULL AND source='live';

                CREATE TABLE IF NOT EXISTS preview_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id     TEXT NOT NULL,
                    song_key     TEXT,
                    previewed_at TEXT NOT NULL,
                    source       TEXT NOT NULL DEFAULT 'live',
                    artist       TEXT,
                    title        TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_preview_events_media ON preview_events(media_id);

                CREATE TABLE IF NOT EXISTS version_notes (
                    media_id   TEXT PRIMARY KEY,
                    note       TEXT,
                    label      TEXT,
                    artist     TEXT,
                    title      TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        # Additive column migrations: none yet. When a column is later added,
        # follow RotationStore's `PRAGMA table_info(...)` + ALTER TABLE loop.


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


_NULLCTX = _NullCtx()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/tests/unit/test_stats_store.py
git commit -m "feat(stats): StatsStore schema + connection idiom"
```

---

### Task 2: `record_play` + dedup

**Files:**
- Modify: `kj-controller/stats_store.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`

**Interfaces:**
- Consumes: `StatsStore` from Task 1.
- Produces: `record_play(media_id, *, entry_id=None, singer=None, artist=None, title=None, song_key=None, played_at=None, night_date=None, source='live') -> bool` (True if a row was inserted). Dedup: one live row per `entry_id`; when `entry_id is None` and live, a 120 s same-`media_id` window guard.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_stats_store.py
def _count(store, table):
    return store._get_conn().execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


def test_record_play_inserts(store):
    assert store.record_play("yt-abc", entry_id=1, singer="Celeste",
                             artist="ABBA", title="SOS", song_key="abba sos") is True
    assert _count(store, "play_events") == 1


def test_record_play_dedups_same_entry(store):
    assert store.record_play("yt-abc", entry_id=7) is True
    assert store.record_play("yt-abc", entry_id=7) is False   # re-press same entry
    assert _count(store, "play_events") == 1


def test_record_play_distinct_entries_both_count(store):
    store.record_play("yt-abc", entry_id=7)
    store.record_play("yt-abc", entry_id=8)   # different singer/slot
    assert _count(store, "play_events") == 2


def test_record_play_no_entry_window_dedups(store):
    assert store.record_play("db-FBK-x", entry_id=None) is True
    assert store.record_play("db-FBK-x", entry_id=None) is False  # within 120s
    assert _count(store, "play_events") == 1


def test_record_play_empty_media_id_noop(store):
    assert store.record_play("", entry_id=1) is False
    assert _count(store, "play_events") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k record_play -v`
Expected: FAIL with `AttributeError: 'StatsStore' object has no attribute 'record_play'`

- [ ] **Step 3: Write minimal implementation**

Add inside `class StatsStore` (after `init_schema`):

```python
    def record_play(self, media_id, *, entry_id=None, singer=None, artist=None,
                    title=None, song_key=None, played_at=None, night_date=None,
                    source="live"):
        if not media_id:
            return False
        conn = self._get_conn()
        with self._lock():
            if entry_id is None and source == "live":
                dup = conn.execute(
                    "SELECT 1 FROM play_events WHERE media_id=? AND entry_id IS NULL "
                    "AND source='live' AND played_at >= datetime('now','-120 seconds') "
                    "LIMIT 1", (media_id,)).fetchone()
                if dup:
                    return False
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO play_events
                    (media_id, song_key, singer, singer_norm, played_at, night_date,
                     entry_id, source, artist, title)
                VALUES (?,?,?,?,
                        COALESCE(?, datetime('now')),
                        COALESCE(?, date('now','localtime')),
                        ?,?,?,?)
                """,
                (media_id, song_key, singer, _norm_singer(singer),
                 played_at, night_date, entry_id, source, artist, title))
            conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k record_play -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/tests/unit/test_stats_store.py
git commit -m "feat(stats): record_play with per-entry + time-window dedup"
```

---

### Task 3: `record_preview` + dedup

**Files:**
- Modify: `kj-controller/stats_store.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`

**Interfaces:**
- Produces: `record_preview(media_id, *, artist=None, title=None, song_key=None, source='live') -> bool`. Dedup: 60 s same-`media_id` window for live.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_stats_store.py
def test_record_preview_inserts_and_windows(store):
    assert store.record_preview("yt-abc", title="ABBA - SOS", song_key="abba sos") is True
    assert store.record_preview("yt-abc") is False            # within 60s window
    assert _count(store, "preview_events") == 1


def test_record_preview_empty_noop(store):
    assert store.record_preview("") is False
    assert _count(store, "preview_events") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k record_preview -v`
Expected: FAIL with `AttributeError: ... 'record_preview'`

- [ ] **Step 3: Write minimal implementation**

Add inside `class StatsStore`:

```python
    def record_preview(self, media_id, *, artist=None, title=None,
                       song_key=None, source="live"):
        if not media_id:
            return False
        conn = self._get_conn()
        with self._lock():
            if source == "live":
                dup = conn.execute(
                    "SELECT 1 FROM preview_events WHERE media_id=? AND source='live' "
                    "AND previewed_at >= datetime('now','-60 seconds') LIMIT 1",
                    (media_id,)).fetchone()
                if dup:
                    return False
            cur = conn.execute(
                """
                INSERT INTO preview_events (media_id, song_key, previewed_at, source, artist, title)
                VALUES (?,?, datetime('now'), ?,?,?)
                """,
                (media_id, song_key, source, artist, title))
            conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k record_preview -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/tests/unit/test_stats_store.py
git commit -m "feat(stats): record_preview with time-window dedup"
```

---

### Task 4: `stats_for` + `usual_media_id` (read aggregates)

**Files:**
- Modify: `kj-controller/stats_store.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`

**Interfaces:**
- Produces:
  - `stats_for(media_ids) -> {media_id: {"plays": int, "previews": int, "last_played": str|None}}` (every requested id present, zero-filled).
  - `usual_media_id(media_ids) -> str|None` — id with most plays in the set, ties broken by most-recent, `None` if all zero.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_stats_store.py
def test_stats_for_zero_fills_and_counts(store):
    store.record_play("yt-a", entry_id=1)
    store.record_play("yt-a", entry_id=2)
    store.record_preview("yt-a")
    out = store.stats_for(["yt-a", "yt-missing"])
    assert out["yt-a"]["plays"] == 2
    assert out["yt-a"]["previews"] == 1
    assert out["yt-a"]["last_played"] is not None
    assert out["yt-missing"] == {"plays": 0, "previews": 0, "last_played": None}


def test_stats_for_empty(store):
    assert store.stats_for([]) == {}


def test_usual_media_id_picks_max(store):
    store.record_play("yt-a", entry_id=1)
    store.record_play("yt-b", entry_id=2)
    store.record_play("yt-b", entry_id=3)
    assert store.usual_media_id(["yt-a", "yt-b"]) == "yt-b"


def test_usual_media_id_none_when_all_zero(store):
    assert store.usual_media_id(["yt-a", "yt-b"]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k "stats_for or usual" -v`
Expected: FAIL with `AttributeError: ... 'stats_for'`

- [ ] **Step 3: Write minimal implementation**

Add inside `class StatsStore`:

```python
    def stats_for(self, media_ids):
        ids = list({m for m in media_ids if m})
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        out = {m: {"plays": 0, "previews": 0, "last_played": None} for m in ids}
        conn = self._get_conn()
        with self._lock():
            for r in conn.execute(
                f"SELECT media_id, COUNT(*) c, MAX(played_at) lp FROM play_events "
                f"WHERE media_id IN ({ph}) GROUP BY media_id", ids):
                out[r["media_id"]]["plays"] = r["c"]
                out[r["media_id"]]["last_played"] = r["lp"]
            for r in conn.execute(
                f"SELECT media_id, COUNT(*) c FROM preview_events "
                f"WHERE media_id IN ({ph}) GROUP BY media_id", ids):
                out[r["media_id"]]["previews"] = r["c"]
        return out

    def usual_media_id(self, media_ids):
        ids = list({m for m in media_ids if m})
        if not ids:
            return None
        ph = ",".join("?" * len(ids))
        conn = self._get_conn()
        with self._lock():
            r = conn.execute(
                f"SELECT media_id, COUNT(*) c FROM play_events WHERE media_id IN ({ph}) "
                f"GROUP BY media_id ORDER BY c DESC, MAX(played_at) DESC LIMIT 1",
                ids).fetchone()
        return r["media_id"] if r and r["c"] > 0 else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k "stats_for or usual" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/tests/unit/test_stats_store.py
git commit -m "feat(stats): stats_for + usual_media_id read aggregates"
```

---

### Task 5: Version notes (`get_note` / `upsert_note` / `distinct_labels`)

**Files:**
- Modify: `kj-controller/stats_store.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`

**Interfaces:**
- Produces:
  - `get_note(media_id) -> dict|None` (columns of `version_notes`).
  - `upsert_note(media_id, note, label, artist=None, title=None) -> dict` (returns the saved note; preserves original artist/title if already set).
  - `distinct_labels() -> [str]` (sorted, non-empty).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_stats_store.py
def test_upsert_note_creates_then_edits(store):
    n = store.upsert_note("yt-a", "censored version", "censored",
                          artist="ABBA", title="SOS")
    assert n["note"] == "censored version" and n["label"] == "censored"
    n2 = store.upsert_note("yt-a", "edited", "video-bg")
    assert n2["note"] == "edited" and n2["label"] == "video-bg"
    assert n2["artist"] == "ABBA"          # preserved
    assert _count(store, "version_notes") == 1


def test_get_note_missing(store):
    assert store.get_note("nope") is None


def test_distinct_labels(store):
    store.upsert_note("yt-a", "x", "censored")
    store.upsert_note("yt-b", "y", "video-bg")
    store.upsert_note("yt-c", "z", "")     # blank excluded
    assert store.distinct_labels() == ["censored", "video-bg"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k "note or label" -v`
Expected: FAIL with `AttributeError: ... 'upsert_note'`

- [ ] **Step 3: Write minimal implementation**

Add inside `class StatsStore` (note: `get_note` is called AFTER releasing the write lock to avoid nesting `_lock`):

```python
    def get_note(self, media_id):
        conn = self._get_conn()
        with self._lock():
            r = conn.execute(
                "SELECT * FROM version_notes WHERE media_id=?", (media_id,)).fetchone()
        return dict(r) if r else None

    def upsert_note(self, media_id, note, label, artist=None, title=None):
        conn = self._get_conn()
        with self._lock():
            conn.execute(
                """
                INSERT INTO version_notes
                    (media_id, note, label, artist, title, created_at, updated_at)
                VALUES (?,?,?,?,?, datetime('now'), datetime('now'))
                ON CONFLICT(media_id) DO UPDATE SET
                    note=excluded.note,
                    label=excluded.label,
                    artist=COALESCE(version_notes.artist, excluded.artist),
                    title=COALESCE(version_notes.title, excluded.title),
                    updated_at=datetime('now')
                """,
                (media_id, note, label, artist, title))
            conn.commit()
        return self.get_note(media_id)

    def distinct_labels(self):
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                "SELECT DISTINCT label FROM version_notes "
                "WHERE label IS NOT NULL AND label <> '' ORDER BY label").fetchall()
        return [r["label"] for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k "note or label" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/tests/unit/test_stats_store.py
git commit -m "feat(stats): version notes upsert/get + distinct labels"
```

---

### Task 6: Leaderboards (`top_songs` / `top_singers`)

**Files:**
- Modify: `kj-controller/stats_store.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`

**Interfaces:**
- Produces:
  - `top_songs(*, singer=None, since=None, limit=10) -> [{"song_key", "artist", "title", "plays"}]` grouped by `song_key`, optional `singer_norm` filter + `played_at >= since`.
  - `top_singers(*, since=None, limit=10) -> [{"singer", "plays", "distinct_songs"}]` grouped by `singer_norm`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_stats_store.py
def test_top_songs_overall_and_by_singer(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", artist="ABBA",
                     title="SOS", song_key="abba sos")
    store.record_play("db-x", entry_id=2, singer="Celeste", artist="ABBA",
                     title="SOS", song_key="abba sos")   # 2nd version, same song
    store.record_play("yt-c", entry_id=3, singer="Dan", artist="Queen",
                     title="Bohemian Rhapsody", song_key="queen bohemian rhapsody")
    overall = store.top_songs(limit=10)
    assert overall[0]["song_key"] == "abba sos" and overall[0]["plays"] == 2
    celeste = store.top_songs(singer="celeste", limit=10)
    assert len(celeste) == 1 and celeste[0]["song_key"] == "abba sos"


def test_top_singers(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", song_key="s1")
    store.record_play("yt-b", entry_id=2, singer="Celeste", song_key="s2")
    store.record_play("yt-c", entry_id=3, singer="Dan", song_key="s1")
    top = store.top_singers(limit=10)
    assert top[0]["singer"] == "Celeste" and top[0]["plays"] == 2
    assert top[0]["distinct_songs"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -k "top_songs or top_singers" -v`
Expected: FAIL with `AttributeError: ... 'top_songs'`

- [ ] **Step 3: Write minimal implementation**

Add inside `class StatsStore`:

```python
    def top_songs(self, *, singer=None, since=None, limit=10):
        clauses = ["song_key IS NOT NULL AND song_key <> ''"]
        params = []
        if singer:
            clauses.append("singer_norm=?")
            params.append(_norm_singer(singer))
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT song_key, COUNT(*) plays, MAX(artist) artist, MAX(title) title
                    FROM play_events WHERE {where}
                    GROUP BY song_key ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def top_singers(self, *, since=None, limit=10):
        clauses = ["singer_norm IS NOT NULL AND singer_norm <> ''"]
        params = []
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT MAX(singer) singer, COUNT(*) plays,
                           COUNT(DISTINCT song_key) distinct_songs
                    FROM play_events WHERE {where}
                    GROUP BY singer_norm ORDER BY plays DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_stats_store.py -v`
Expected: PASS (all StatsStore tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/tests/unit/test_stats_store.py
git commit -m "feat(stats): top_songs + top_singers leaderboards"
```

---

### Task 7: Wire `flask_app.stats` into the app factory

**Files:**
- Modify: `kj-controller/app.py:15` (import), `kj-controller/app.py:201`, `kj-controller/app.py:390` (both factory paths)

**Interfaces:**
- Consumes: `StatsStore` (Task 1).
- Produces: `current_app.stats` available in routes (same DB file as `current_app.media_library`).

- [ ] **Step 1: Add the import**

Near `from media_library import MediaLibraryStore` (`app.py:15`) add:

```python
from stats_store import StatsStore
```

- [ ] **Step 2: Instantiate in both factory paths**

Immediately after each `flask_app.media_library = MediaLibraryStore(cfg.get('media_db_path'))` (at `app.py:201` and `app.py:390`), add:

```python
    flask_app.stats = StatsStore(cfg.get('media_db_path'))
```

- [ ] **Step 3: Verify app still boots (smoke)**

Run: `cd kj-controller && python -c "import app; a = app.create_app(); print(type(a.stats).__name__)"`
Expected: prints `StatsStore` (or, if `create_app` needs args, run the existing app boot test: `pytest tests/ -k "create_app or flask_app" -q` → PASS)

- [ ] **Step 4: Commit**

```bash
git add kj-controller/app.py
git commit -m "feat(stats): wire StatsStore onto the Flask app (shared media_library.db)"
```

---

### Task 8: Record plays on `/play` (+ thread `entry_id` from frontend)

**Files:**
- Modify: `kj-controller/routes.py` (add `_record_play_stat` helper; call it in `handle_play` before the `return` at line ~601)
- Modify: `kj-controller/static/app.js:415` (`playMedia` signature) and `:4981` (rotation call site)
- Test: `kj-controller/tests/unit/test_routes_stats.py`

**Interfaces:**
- Consumes: `current_app.stats.record_play`, `current_app.media_library.get_by_path`, `current_app.rotation.store.get_entry`, `naming.extract_media_id`, `_normalize_song_key`.
- Produces: a play event per `/play` that resolves to a `media_id`; `playMedia(filePath, entryId)` sends `entry_id`.

- [ ] **Step 1: Write the failing test**

```python
# kj-controller/tests/unit/test_routes_stats.py
import routes


class _FakeStats:
    def __init__(self):
        self.plays = []
    def record_play(self, media_id, **kw):
        self.plays.append((media_id, kw)); return True


class _FakeML:
    def __init__(self, by_path):
        self._by_path = by_path
    def get_by_path(self, p):
        return self._by_path.get(p)


class _FakeStore:
    def get_entry(self, eid):
        return {"id": eid, "singer": "Celeste", "song_artist": "ABBA - SOS"}


class _FakeRotation:
    store = _FakeStore()


def test_record_play_stat_resolves_and_records(app_ctx):
    # app_ctx: a pushed Flask app context with current_app.stats/media_library/rotation set
    from flask import current_app
    current_app.stats = _FakeStats()
    current_app.media_library = _FakeML(
        {"/opt/nomad/downloads/x.mp4": {"media_id": "yt-abc", "artist": "ABBA", "title": "SOS"}})
    current_app.rotation = _FakeRotation()
    routes._record_play_stat("/opt/nomad/downloads/x.mp4", 42)
    assert current_app.stats.plays[0][0] == "yt-abc"
    kw = current_app.stats.plays[0][1]
    assert kw["entry_id"] == 42 and kw["singer"] == "Celeste"


def test_record_play_stat_unresolved_is_noop(app_ctx):
    from flask import current_app
    current_app.stats = _FakeStats()
    current_app.media_library = _FakeML({})   # path not known, no [media_id] in name
    current_app.rotation = None
    routes._record_play_stat("/opt/nomad/downloads/plain name.mp4", None)
    assert current_app.stats.plays == []
```

Add an `app_ctx` fixture to `tests/unit/conftest.py` (or the local test file) if not present:

```python
# tests/unit/conftest.py  (add if missing)
import pytest


@pytest.fixture
def app_ctx():
    import app as app_module
    flask_app = app_module.create_app()
    with flask_app.app_context():
        yield flask_app
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k record_play_stat -v`
Expected: FAIL with `AttributeError: module 'routes' has no attribute '_record_play_stat'`

- [ ] **Step 3: Write the helper + wire it in**

Add near the top of `routes.py` (after imports / other module helpers):

```python
def _record_play_stat(validated_path, entry_id):
    """Best-effort: credit one play for the media_id at validated_path. Never raises."""
    try:
        stats = getattr(current_app, 'stats', None)
        ml = getattr(current_app, 'media_library', None)
        if not stats:
            return
        row = ml.get_by_path(validated_path) if ml else None
        media_id = (row or {}).get('media_id')
        artist = (row or {}).get('artist')
        title = (row or {}).get('title')
        if not media_id:
            from naming import extract_media_id
            media_id = extract_media_id(os.path.basename(validated_path))
        if not media_id:
            return
        singer = None
        rotation = getattr(current_app, 'rotation', None)
        if entry_id and rotation:
            entry = rotation.store.get_entry(entry_id)
            if entry:
                singer = entry.get('singer')
                if not (artist or title):
                    artist = entry.get('song_artist')
        song_key = _normalize_song_key(artist, title)
        stats.record_play(media_id, entry_id=entry_id, singer=singer,
                          artist=artist, title=title, song_key=song_key)
    except Exception as e:  # never let stats break playback
        try:
            log_message(f"stats: play record failed: {e}", current_app.kj_config)
        except Exception:
            pass
```

In `handle_play`, replace the final `return jsonify({"success": True, "message": "Playback initiated."})` (line ~601) with:

```python
    _record_play_stat(validated, request.json.get('entry_id'))
    return jsonify({"success": True, "message": "Playback initiated."})
```

- [ ] **Step 4: Thread `entry_id` through the frontend**

In `static/app.js`, change `playMedia` (line ~415):

```javascript
async function playMedia(filePath, entryId) {
    // ...existing body up to the apiCall...
    await apiCall('/play', entryId != null ? { file_path: filePath, entry_id: entryId }
                                            : { file_path: filePath });
    // ...rest unchanged...
}
```

And the rotation play call site (line ~4981):

```javascript
    playMedia(entry.file_path, entry.id);
```

(Other `playMedia(...)` call sites — Available Songs etc. — stay as-is; they pass no `entryId` and fall back to the time-window dedup.)

- [ ] **Step 5: Run tests**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k record_play_stat -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/static/app.js kj-controller/tests/unit/test_routes_stats.py kj-controller/tests/unit/conftest.py
git commit -m "feat(stats): record plays on /play, thread entry_id from rotation"
```

---

### Task 9: Record previews on `/preview/resolve`

**Files:**
- Modify: `kj-controller/routes.py` (`preview_resolve` at line ~4442; add `_youtube_id` + `_record_preview_stat` helpers)
- Test: `kj-controller/tests/unit/test_routes_stats.py`

**Interfaces:**
- Consumes: `current_app.stats.record_preview`, `current_app.media_library.get_by_path`.
- Produces: a preview event per resolvable descriptor. `_youtube_id(url) -> str|None` (11-char id).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_routes_stats.py
def test_youtube_id_extraction():
    assert routes._youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert routes._youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert routes._youtube_id("not a url") is None


class _FakePreviewStats:
    def __init__(self):
        self.previews = []
    def record_preview(self, media_id, **kw):
        self.previews.append((media_id, kw)); return True


def test_record_preview_stat_youtube(app_ctx):
    from flask import current_app
    current_app.stats = _FakePreviewStats()
    routes._record_preview_stat(
        {"source": "youtube", "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
         "title": "Rick Astley - Never Gonna Give You Up"})
    assert current_app.stats.previews[0][0] == "yt-dQw4w9WgXcQ"


def test_record_preview_stat_local(app_ctx):
    from flask import current_app
    current_app.stats = _FakePreviewStats()
    current_app.media_library = _FakeML(
        {"/opt/nomad/downloads/x.mp4": {"media_id": "gen-abcd1234",
                                        "artist": "A", "title": "T"}})
    routes._record_preview_stat({"source": "local", "file_path": "/opt/nomad/downloads/x.mp4"})
    assert current_app.stats.previews[0][0] == "gen-abcd1234"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k "youtube_id or record_preview_stat" -v`
Expected: FAIL with `AttributeError: ... '_youtube_id'`

- [ ] **Step 3: Write helpers + wire into `preview_resolve`**

Add to `routes.py`:

```python
_YT_URL_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")


def _youtube_id(url):
    m = _YT_URL_RE.search(url or "")
    return m.group(1) if m else None


def _record_preview_stat(descriptor):
    """Best-effort: credit one preview for the descriptor's media_id. Never raises."""
    try:
        stats = getattr(current_app, 'stats', None)
        if not stats or not isinstance(descriptor, dict):
            return
        ml = getattr(current_app, 'media_library', None)
        source = descriptor.get('source')
        title = descriptor.get('title')
        artist = None
        media_id = None
        if source == 'local' and ml:
            row = ml.get_by_path(descriptor.get('file_path')) or {}
            media_id = row.get('media_id')
            artist = row.get('artist')
            title = title or row.get('title')
        elif source == 'youtube':
            vid = _youtube_id(descriptor.get('youtube_url'))
            if vid:
                media_id = f"yt-{vid}"
        # divebar previews: identity is the Phase-2-dependent fuzzy case (spec §10);
        # skip counting rather than guess. Tighten once P2 lands file_id-based ids.
        if not media_id:
            return
        song_key = _normalize_song_key(artist, title)
        stats.record_preview(media_id, artist=artist, title=title, song_key=song_key)
    except Exception as e:
        try:
            log_message(f"stats: preview record failed: {e}", current_app.kj_config)
        except Exception:
            pass
```

Modify `preview_resolve` (line ~4442):

```python
def preview_resolve():
    descriptor = request.get_json(silent=True) or {}
    if not isinstance(descriptor, dict):
        return jsonify({"mode": "unavailable", "reason": "Invalid request"}), 400
    result = current_app.preview.resolve(descriptor)
    _record_preview_stat(descriptor)
    return jsonify(result)
```

Ensure `import re` exists at the top of `routes.py` (it almost certainly does; add if missing).

- [ ] **Step 4: Run tests**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k "youtube_id or record_preview_stat" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): record previews on /preview/resolve (youtube+local)"
```

---

### Task 10: Notes API (`POST /media/note`, `GET /media/note-labels`)

**Files:**
- Modify: `kj-controller/routes.py` (two new routes on `routes_bp`)
- Test: `kj-controller/tests/unit/test_routes_stats.py`

**Interfaces:**
- Consumes: `current_app.stats.upsert_note`, `.distinct_labels`.
- Produces: `POST /media/note` `{media_id, note, label, artist?, title?}` → `{"note": {...}}`; `GET /media/note-labels` → `{"labels": [...]}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_routes_stats.py  (uses the app's real test client + StatsStore)
def test_media_note_upsert_and_labels(flask_test_client):
    r = flask_test_client.post("/media/note", json={
        "media_id": "yt-abc", "note": "censored version", "label": "censored"})
    assert r.status_code == 200
    assert r.get_json()["note"]["note"] == "censored version"
    r2 = flask_test_client.get("/media/note-labels")
    assert "censored" in r2.get_json()["labels"]


def test_media_note_requires_media_id(flask_test_client):
    r = flask_test_client.post("/media/note", json={"note": "x"})
    assert r.status_code == 400
```

(`flask_test_client` is the existing fixture in `tests/conftest.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k media_note -v`
Expected: FAIL (404 — routes not registered)

- [ ] **Step 3: Add the routes**

```python
@routes_bp.route('/media/note', methods=['POST'])
def media_note():
    data = request.get_json(silent=True) or {}
    media_id = (data.get('media_id') or '').strip()
    if not media_id:
        return jsonify({"error": "media_id is required"}), 400
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"error": "stats unavailable"}), 503
    note = stats.upsert_note(
        media_id, data.get('note') or '', (data.get('label') or '').strip() or None,
        artist=data.get('artist'), title=data.get('title'))
    return jsonify({"note": note})


@routes_bp.route('/media/note-labels', methods=['GET'])
def media_note_labels():
    stats = getattr(current_app, 'stats', None)
    return jsonify({"labels": stats.distinct_labels() if stats else []})
```

- [ ] **Step 4: Run tests**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k media_note -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): notes API (POST /media/note, GET /media/note-labels)"
```

---

### Task 11: Historic backfill script

**Files:**
- Create: `kj-controller/scripts/backfill_play_stats.py`
- Test: `kj-controller/tests/unit/test_backfill_play_stats.py`

**Interfaces:**
- Consumes: `StatsStore`, `MediaLibraryStore`, `naming.extract_media_id`, `text_normalize.group_key`.
- Produces: `backfill(rotation_db_path, media_db_path, *, execute=False) -> {"attributed": int, "skipped": int}`; a `__main__` CLI with `--rotation-db`, `--media-db`, `--execute` (default dry-run).

- [ ] **Step 1: Write the failing test**

```python
# kj-controller/tests/unit/test_backfill_play_stats.py
import sqlite3
import importlib.util
import os

HERE = os.path.dirname(__file__)
SPEC = importlib.util.spec_from_file_location(
    "backfill_play_stats",
    os.path.join(HERE, "..", "..", "scripts", "backfill_play_stats.py"))


def _load():
    mod = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(mod)
    return mod


def _make_archive(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE rotation_archive (
        night_date TEXT, singer TEXT, song_artist TEXT, status TEXT,
        notes TEXT, position INTEGER, file_path TEXT, duration REAL, created_at TEXT)""")
    conn.executemany(
        "INSERT INTO rotation_archive (night_date, singer, song_artist, status, file_path)"
        " VALUES (?,?,?,?,?)",
        [("2026-03-27", "Celeste", "ABBA - SOS", "Done", "/opt/nomad/downloads/x.mp4"),
         ("2026-03-27", "Dan", "Queen - Bohemian Rhapsody", "Done", None),        # no path
         ("2026-03-27", "Amy", "ABBA - SOS", "Waiting", "/opt/nomad/downloads/x.mp4")])  # not Done
    conn.commit(); conn.close()


def test_backfill_attributes_done_rows(tmp_path):
    mod = _load()
    from media_library import MediaLibraryStore
    rot = str(tmp_path / "rotation.db"); mldb = str(tmp_path / "media_library.db")
    _make_archive(rot)
    ml = MediaLibraryStore(mldb)
    ml.upsert({"media_id": "yt-x", "source": "youtube", "artist": "ABBA",
               "title": "SOS", "file_path": "/opt/nomad/downloads/x.mp4"})
    res = mod.backfill(rot, mldb, execute=True)
    assert res["attributed"] == 1        # only the Done row with a resolvable path
    assert res["skipped"] == 1           # Done row with no path (Waiting row ignored entirely)

    from stats_store import StatsStore
    s = StatsStore(mldb)
    assert s.stats_for(["yt-x"])["yt-x"]["plays"] == 1


def test_backfill_idempotent(tmp_path):
    mod = _load()
    from media_library import MediaLibraryStore
    from stats_store import StatsStore
    rot = str(tmp_path / "rotation.db"); mldb = str(tmp_path / "media_library.db")
    _make_archive(rot)
    MediaLibraryStore(mldb).upsert({"media_id": "yt-x", "source": "youtube",
        "artist": "ABBA", "title": "SOS", "file_path": "/opt/nomad/downloads/x.mp4"})
    mod.backfill(rot, mldb, execute=True)
    mod.backfill(rot, mldb, execute=True)     # re-run
    assert StatsStore(mldb).stats_for(["yt-x"])["yt-x"]["plays"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_backfill_play_stats.py -v`
Expected: FAIL (script file does not exist)

- [ ] **Step 3: Write the script**

```python
# kj-controller/scripts/backfill_play_stats.py
"""One-off: backfill historic play_events from rotation_archive 'Done' rows.

Dry-run by default; --execute wipes prior backfill rows then re-inserts (idempotent).
Run on device off-show:
    python scripts/backfill_play_stats.py --rotation-db ~/kjdata/rotation.db \
        --media-db ~/kjdata/media_library.db            # dry-run report
    python scripts/backfill_play_stats.py ... --execute
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from media_library import MediaLibraryStore   # noqa: E402
from stats_store import StatsStore             # noqa: E402
from naming import extract_media_id            # noqa: E402
from text_normalize import group_key           # noqa: E402


def _resolve_media_id(ml, file_path):
    if not file_path:
        return None, None, None
    row = ml.get_by_path(file_path) or {}
    mid = row.get("media_id") or extract_media_id(os.path.basename(file_path))
    return mid, row.get("artist"), row.get("title")


def _split_artist_title(song_artist):
    if song_artist and " - " in song_artist:
        a, t = song_artist.split(" - ", 1)
        return a.strip(), t.strip()
    return None, (song_artist or None)


def backfill(rotation_db_path, media_db_path, *, execute=False):
    ml = MediaLibraryStore(media_db_path)
    stats = StatsStore(media_db_path)
    conn = sqlite3.connect(rotation_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT night_date, singer, song_artist, file_path "
        "FROM rotation_archive WHERE status='Done'").fetchall()
    conn.close()

    if execute:
        c = stats._get_conn()
        with stats._lock():
            c.execute("DELETE FROM play_events WHERE source='backfill'")
            c.commit()

    attributed = skipped = 0
    for r in rows:
        mid, a, t = _resolve_media_id(ml, r["file_path"])
        if not mid:
            skipped += 1
            continue
        if not (a or t):
            a, t = _split_artist_title(r["song_artist"])
        if execute:
            stats.record_play(
                mid, singer=r["singer"], artist=a, title=t,
                song_key=group_key(a, t),
                played_at=r["night_date"], night_date=r["night_date"],
                source="backfill")
        attributed += 1
    return {"attributed": attributed, "skipped": skipped}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rotation-db", default=os.path.expanduser("~/kjdata/rotation.db"))
    p.add_argument("--media-db", default=os.path.expanduser("~/kjdata/media_library.db"))
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    res = backfill(args.rotation_db, args.media_db, execute=args.execute)
    mode = "EXECUTED" if args.execute else "DRY-RUN"
    print(f"[{mode}] attributed={res['attributed']} skipped={res['skipped']}")


if __name__ == "__main__":
    main()
```

Note: `source='backfill'` rows carry no `entry_id`, so they bypass the live per-entry unique index. Re-running `--execute` deletes prior backfill rows first → idempotent.

- [ ] **Step 4: Run tests**

Run: `cd kj-controller && pytest tests/unit/test_backfill_play_stats.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/scripts/backfill_play_stats.py kj-controller/tests/unit/test_backfill_play_stats.py
git commit -m "feat(stats): idempotent historic backfill from rotation_archive Done rows"
```

---

### Task 12: Stats view — endpoints + KJ panel

**Files:**
- Modify: `kj-controller/routes.py` (`GET /stats/top-songs`, `GET /stats/singers`)
- Modify: `kj-controller/static/app.js` (Stats panel: fetch + render), `kj-controller/static/style.css`, `kj-controller/templates/index.html`
- Test: `kj-controller/tests/unit/test_routes_stats.py`

**Interfaces:**
- Consumes: `current_app.stats.top_songs`, `.top_singers`.
- Produces: `GET /stats/top-songs?singer=&since=&limit=` → `{"songs": [...]}`; `GET /stats/singers?since=&limit=` → `{"singers": [...]}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_routes_stats.py
def test_stats_endpoints(flask_test_client):
    # Record on the SAME app the client serves, so the endpoint sees it.
    flask_test_client.application.stats.record_play(
        "yt-a", entry_id=101, singer="Celeste", artist="ABBA", title="SOS", song_key="abba sos")
    r = flask_test_client.get("/stats/top-songs?limit=5")
    assert r.status_code == 200
    assert r.get_json()["songs"][0]["song_key"] == "abba sos"
    r2 = flask_test_client.get("/stats/singers")
    assert any(s["singer"] == "Celeste" for s in r2.get_json()["singers"])
    r3 = flask_test_client.get("/stats/top-songs?singer=celeste")
    assert r3.get_json()["songs"][0]["song_key"] == "abba sos"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k stats_endpoints -v`
Expected: FAIL (404)

- [ ] **Step 3: Add the endpoints**

```python
@routes_bp.route('/stats/top-songs', methods=['GET'])
def stats_top_songs():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"songs": []})
    singer = request.args.get('singer') or None
    since = request.args.get('since') or None
    try:
        limit = min(int(request.args.get('limit', 10)), 100)
    except (TypeError, ValueError):
        limit = 10
    return jsonify({"songs": stats.top_songs(singer=singer, since=since, limit=limit)})


@routes_bp.route('/stats/singers', methods=['GET'])
def stats_singers():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"singers": []})
    since = request.args.get('since') or None
    try:
        limit = min(int(request.args.get('limit', 50)), 200)
    except (TypeError, ValueError):
        limit = 50
    return jsonify({"singers": stats.top_singers(since=since, limit=limit)})
```

- [ ] **Step 4: Add the Stats panel (frontend)**

In `templates/index.html`, add a Stats panel container (near the other panels/tabs — match the existing panel markup pattern):

```html
<div id="statsPanel" class="panel" style="display:none;">
  <h2>Stats</h2>
  <div class="stats-controls">
    <label>Since <input type="date" id="statsSince"></label>
    <input type="text" id="statsSinger" list="statsSingerList" placeholder="Filter by singer…">
    <datalist id="statsSingerList"></datalist>
    <button id="statsRefresh">Refresh</button>
  </div>
  <h3>Top songs</h3>
  <ol id="statsTopSongs" class="stats-list"></ol>
  <h3>Top singers</h3>
  <ol id="statsTopSingers" class="stats-list"></ol>
</div>
```

In `static/app.js`, add:

```javascript
async function loadStats() {
    const since = document.getElementById('statsSince').value;
    const singer = document.getElementById('statsSinger').value.trim();
    const qs = (extra) => {
        const p = new URLSearchParams();
        if (since) p.set('since', since);
        Object.entries(extra || {}).forEach(([k, v]) => v && p.set(k, v));
        return p.toString() ? '?' + p.toString() : '';
    };
    const [songsResp, singersResp] = await Promise.all([
        fetch('/stats/top-songs' + qs({ singer, limit: 10 })),
        fetch('/stats/singers' + qs({ limit: 50 })),
    ]);
    const songs = (await songsResp.json()).songs || [];
    const singers = (await singersResp.json()).singers || [];
    document.getElementById('statsTopSongs').innerHTML = songs.map(s =>
        `<li>${escapeHtml(s.artist || '')}${s.artist ? ' – ' : ''}${escapeHtml(s.title || s.song_key)} — ▶ ${s.plays}</li>`
    ).join('') || '<li class="muted">No plays recorded yet.</li>';
    document.getElementById('statsTopSingers').innerHTML = singers.map(s =>
        `<li>${escapeHtml(s.singer)} — ▶ ${s.plays} · ${s.distinct_songs} songs</li>`
    ).join('') || '<li class="muted">No plays recorded yet.</li>';
    document.getElementById('statsSingerList').innerHTML = singers.map(s =>
        `<option value="${escapeHtml(s.singer)}">`).join('');
}

document.getElementById('statsRefresh')?.addEventListener('click', loadStats);
document.getElementById('statsSinger')?.addEventListener('change', loadStats);
```

Wire the panel into the existing show/hide navigation (follow whatever `showPanel`/tab pattern `app.js` already uses; call `loadStats()` when the Stats panel is shown). Use the existing `escapeHtml` helper if present; if not, add a minimal one.

In `static/style.css`, add:

```css
.stats-controls { display: flex; gap: .5rem; align-items: center; margin-bottom: .75rem; flex-wrap: wrap; }
.stats-list { margin: .25rem 0 1rem; padding-left: 1.5rem; }
.stats-list li { padding: .15rem 0; }
```

- [ ] **Step 5: Run tests + JS syntax hook**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k stats_endpoints -v`
Expected: PASS
Run: `node --check static/app.js`
Expected: no output (valid syntax)

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/static/app.js kj-controller/static/style.css kj-controller/templates/index.html kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): leaderboard endpoints + KJ Stats panel"
```

---

### Task 13: Rotation-search row enrichment + version badges/notes (⚠️ Phase-3 overlap — build additive, last)

**Files:**
- Modify: `kj-controller/routes.py` (`rotation_search` at ~3655: `_enrich_search_stats` + `resolve_row_media_id`)
- Modify: `kj-controller/static/app.js` (row renderers under `renderRotSearchDropdown` ~5452: `renderStatsBadges`, note chip, note modal)
- Modify: `kj-controller/static/style.css`, `kj-controller/pyproject.toml` (version bump)
- Test: `kj-controller/tests/unit/test_routes_stats.py`

**Interfaces:**
- Consumes: `current_app.stats.stats_for`, `.usual_media_id`, `.get_note`; `current_app.media_library.get_by_path`; `naming.media_id_for`; `_youtube_id`.
- Produces: each row in `/rotation/search` `local`/`karaoke_nerds[].tracks`/`divebar` gains a `stats` block `{plays, previews, last_played, is_usual, note, label}`. `resolve_row_media_id(row, kind, ml) -> str|None`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_routes_stats.py
def test_resolve_row_media_id(app_ctx):
    from flask import current_app
    current_app.media_library = _FakeML(
        {"/opt/nomad/downloads/x.mp4": {"media_id": "gen-abcd1234"}})
    ml = current_app.media_library
    assert routes.resolve_row_media_id({"path": "/opt/nomad/downloads/x.mp4"}, "local", ml) == "gen-abcd1234"
    assert routes.resolve_row_media_id(
        {"youtube_url": "https://youtu.be/dQw4w9WgXcQ"}, "kn", ml) == "yt-dQw4w9WgXcQ"


def test_rotation_search_enriches_stats(flask_test_client, monkeypatch):
    app = flask_test_client.application
    # a played local version, on the SAME app the client serves:
    app.stats.record_play("gen-abcd1234", entry_id=201, artist="A", title="T", song_key="a t")
    app.media_library = _FakeML({"/opt/nomad/downloads/x.mp4": {"media_id": "gen-abcd1234"}})
    # stub unified_search so the test doesn't hit the network (KN/divebar):
    monkeypatch.setattr(routes, "unified_search", lambda q, a: {
        "local": [{"path": "/opt/nomad/downloads/x.mp4", "artist": "A", "title": "T"}],
        "karaoke_nerds": [], "divebar": [], "karaoke_nerds_timeout": False})
    r = flask_test_client.get("/rotation/search?q=abc")
    row = r.get_json()["local"][0]
    assert row["stats"]["plays"] == 1
    assert row["stats"]["is_usual"] is True
    assert row["media_id"] == "gen-abcd1234"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -k "resolve_row or enriches_stats" -v`
Expected: FAIL with `AttributeError: ... 'resolve_row_media_id'`

- [ ] **Step 3: Add resolver + enrichment (backend)**

```python
def resolve_row_media_id(row, kind, ml):
    """Best-effort media_id for a search row. See spec §10. Never raises."""
    try:
        if kind == "local":
            r = (ml.get_by_path(row.get("path")) if ml else None) or {}
            mid = r.get("media_id")
            if mid:
                return mid
            from naming import extract_media_id
            return extract_media_id(os.path.basename(row.get("filename") or row.get("path") or ""))
        if kind == "kn":
            vid = _youtube_id(row.get("youtube_url"))
            return f"yt-{vid}" if vid else None
        if kind == "divebar":
            # Phase-2-dependent fuzzy match: find a media_library community row for the
            # same brand + normalized artist/title. Exact once P2 stores file_id ids.
            if not ml:
                return None
            brand = (row.get("brand") or row.get("brand_code") or "").upper()
            ak = _group_key(row.get("artist"), row.get("title"))
            for rec in ml.list_records(source="community"):
                if (rec.get("media_id") or "").upper().startswith(f"DB-{brand}") \
                        and _group_key(rec.get("artist"), rec.get("title")) == ak:
                    return rec.get("media_id")
            return None
    except Exception:
        return None
    return None


def _enrich_search_stats(result):
    """Attach a `stats` block to each local/KN-track/divebar row. Best-effort."""
    stats = getattr(current_app, 'stats', None)
    ml = getattr(current_app, 'media_library', None)
    if not stats:
        return
    try:
        rows = []   # (row_dict, media_id)
        for r in result.get("local", []):
            rows.append((r, resolve_row_media_id(r, "local", ml)))
        for song in result.get("karaoke_nerds", []):
            for t in song.get("tracks") or []:
                rows.append((t, resolve_row_media_id(t, "kn", ml)))
        for r in result.get("divebar", []):
            rows.append((r, resolve_row_media_id(r, "divebar", ml)))

        ids = [mid for _, mid in rows if mid]
        agg = stats.stats_for(ids)
        # "usual" is per logical song: group this result's rows by song_key.
        by_song = {}
        for row, mid in rows:
            if not mid:
                continue
            sk = _normalize_song_key(row.get("artist"), row.get("title"))
            by_song.setdefault(sk, []).append(mid)
        usual = {}
        for sk, mids in by_song.items():
            u = stats.usual_media_id(mids)
            if u:
                usual[u] = True

        for row, mid in rows:
            if not mid:
                continue
            s = dict(agg.get(mid, {"plays": 0, "previews": 0, "last_played": None}))
            s["is_usual"] = bool(usual.get(mid))
            note = stats.get_note(mid)
            s["note"] = (note or {}).get("note")
            s["label"] = (note or {}).get("label")
            row["stats"] = s
            row["media_id"] = mid   # frontend needs this to edit the note
    except Exception as e:
        try:
            log_message(f"stats: search enrichment failed: {e}", current_app.kj_config)
        except Exception:
            pass
```

In `rotation_search` (after `result = unified_search(...)`, before building `response`):

```python
    _enrich_search_stats(result)
```

and include `divebar` stats already flow because `response` copies `result["divebar"]`.

- [ ] **Step 4: Render badges + note chip + editor (frontend)**

In `static/app.js`, add a badge helper and a note modal, and inject into each row renderer under `renderRotSearchDropdown` (e.g. `renderRotLocalRow` and the KN/divebar row builders — insert `renderStatsBadges(row.stats)` into each row's right-hand tag area, alongside the existing `rotTagsHtml`):

```javascript
function renderStatsBadges(stats) {
    if (!stats) return '';
    const parts = [];
    if (stats.is_usual) parts.push('<span class="stat-usual" title="Version you usually play">⭐</span>');
    if (stats.plays) parts.push(`<span class="stat-plays" title="Plays ever">▶ ${stats.plays}</span>`);
    if (stats.previews) parts.push(`<span class="stat-prev" title="Previews">👁 ${stats.previews}</span>`);
    if (stats.label) parts.push(`<span class="stat-label">${escapeHtml(stats.label)}</span>`);
    if (stats.note) parts.push(`<span class="stat-note" title="${escapeHtml(stats.note)}">📝</span>`);
    return parts.length ? `<span class="stat-badges">${parts.join(' ')}</span>` : '';
}

// Opens a small modal to edit the note+label for a media_id, then re-runs the search.
async function editVersionNote(mediaId, artist, title, current) {
    const labels = (await (await fetch('/media/note-labels')).json()).labels || [];
    const note = await showNoteModal({ mediaId, artist, title,
        note: (current && current.note) || '', label: (current && current.label) || '',
        labelOptions: labels });
    if (note === null) return;               // cancelled
    await fetch('/media/note', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ media_id: mediaId, note: note.note, label: note.label,
                               artist, title }) });
    if (typeof rerunRotSearch === 'function') rerunRotSearch();
}
```

`showNoteModal(...)` returns a Promise of `{note, label}` or `null`. Implement it using the **existing modal pattern in `app.js`** (there are existing modals — reuse that scaffolding rather than `prompt()`), with a `<textarea>` for the note and a text `<input list=…>` bound to a `<datalist>` of `labelOptions`. Wire a small "📝" edit button in each row's badge area that calls
`editVersionNote(row.media_id, row.artist, row.title, row.stats)`. The frontend needs the row's
resolved `media_id`; the backend now exposes it (`row["media_id"] = mid`, added to
`_enrich_search_stats` in Step 3), so no client-side id derivation is required.

`showNoteModal(...)` returns a Promise resolving to `{note, label}` (or `null` if cancelled).
Implement it by reusing whatever modal scaffolding `app.js` already has (grep for existing
`Modal`/`showModal`/dialog helpers — do NOT use `prompt()`), with a `<textarea>` for the note and
a text `<input list="noteLabelList">` bound to a `<datalist>` populated from `labelOptions`.

In `static/style.css`:

```css
.stat-badges { display: inline-flex; gap: .35rem; align-items: center; margin-left: .5rem; }
.stat-usual { color: #ffcf4d; }
.stat-plays { color: #7fd1a1; font-variant-numeric: tabular-nums; }
.stat-prev  { color: #9aa4b2; font-size: .85em; }
.stat-label { background: #2b3442; border-radius: 4px; padding: 0 .35rem; font-size: .8em; }
.stat-note  { cursor: help; }
```

- [ ] **Step 5: Bump version + verify**

Bump the version in `kj-controller/pyproject.toml` (this is the single frontend cache-bust for the whole feature; e.g. minor bump).

Run: `cd kj-controller && pytest tests/unit/test_routes_stats.py -v && node --check static/app.js`
Expected: PASS + no JS syntax errors.
Run: `cd kj-controller && pytest -q`
Expected: full suite PASS.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/static/app.js kj-controller/static/style.css kj-controller/pyproject.toml kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): rotation-search row play counts, usual badge + version notes (Phase-3 overlap)"
```

---

## Post-implementation (manual, off-show)

1. Review with `/coderabbit` (`coderabbit review --agent --type committed`), fix ≤3 cycles.
2. `/pr` (adds `@coderabbitai ignore`), merge (squash).
3. Deploy off-show: `git pull` on device, restart `kj-controller` (interrupts playback — no live event).
4. Backfill: `ssh nomadpctunnel` → `python scripts/backfill_play_stats.py` (dry-run), review the attributed/skipped counts, then `--execute`.
5. Verify: hard-refresh KJ UI (`app.js?v=` bumped) → check counts appear on rotation-search rows and the Stats panel shows top songs/singers (incl. backfilled history).
6. Flag the Phase-3 session (memory + PR note) that `static/app.js` row renderers + `/rotation/search` now carry a `stats` block + `media_id` per row to preserve on rebase.

## Self-Review notes (author)

- **Spec coverage:** §3 identity (T1 keys, T6/T13 song_key, T2/T6 singer) ✓; §4 storage (T1) ✓; §5 recording (T8 play, T9 preview) ✓; §6 backfill (T11) ✓; §7 row display (T13) ✓; §8 notes API (T10) ✓; §9 stats view (T12) ✓; §10 row→media_id (T13 `resolve_row_media_id`) ✓; §11 offline (try/except in T8/T9/T10/T12/T13 helpers) ✓; §12 testing (each task) ✓; §13 deploy (post-impl) ✓.
- **Type consistency:** `record_play`/`record_preview`/`stats_for`/`usual_media_id`/`top_songs`/`top_singers`/`get_note`/`upsert_note`/`distinct_labels`/`resolve_row_media_id` signatures are defined once (T1–T6, T13) and consumed with matching names/args in routes/backfill.
- **Known deferrals (spec-sanctioned):** divebar row/preview `media_id` is fuzzy pre-Phase-2 (T9 skips divebar previews; T13 does a brand+song_key match); reimage durability is a follow-up.
