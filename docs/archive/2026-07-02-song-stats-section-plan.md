# Song Stats Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the cramped "Song Stats" modal into a dedicated, always-present, explorable Song Stats section below the Library section, with multiple views, filters, and clickable drill-downs.

**Architecture:** Add read-only `StatsStore` methods (over the existing `play_events` table) + matching `/stats/*` Flask GET routes, following the store's established idiom (per-thread conn + WAL, non-reentrant `self._lock()`, clamped limits, empty-on-unavailable). Replace the modal with a vanilla-JS `#song-stats` section: overview cards, a segmented view switcher (Top Songs · Top Singers · Top Artists · Nights), a compact filter row, and inline expand drill-downs, all lazy-loaded and bounded to ~40vh internal scroll.

**Tech Stack:** Python 3 + Flask + SQLite (backend); vanilla JS + Jinja2 template + `style.css` (frontend, no build step). Tests: pytest (`:memory:` StatsStore + `flask_test_client`), `node --check`, local mock-Flask Playwright harness.

## Global Constraints

- **Never nest `with self._lock()`** — the memory lock is non-reentrant. Call helper methods only AFTER releasing the lock (like `upsert_note` does). Migrations/backfills that need the lock run in their own locked block, never inside another.
- **Parameterize every user value** in SQL; never f-string user input into queries (only static column/clause fragments).
- **Clamp all route limits** `max(1, min(int(arg), CAP))`; on parse error fall back to the default.
- **Empty-on-unavailable:** every `/stats/*` route does `stats = getattr(current_app, 'stats', None); if not stats: return jsonify({<key>: <empty>})`.
- **Query params, not path params**, for `singer`, `artist`, `song_key`, `night_date` (they contain spaces/punctuation).
- **Escaping (frontend):** `escHtml` in element text content, `escAttr` in HTML attributes (`escHtml` does NOT escape quotes). Any nested clickable inside a row that also has a row-level `onclick` must call `event.stopPropagation()`.
- **Run backend tests with** `rtk proxy python -m pytest <file> -v` (plain pytest output is mangled by a repo shell hook; there is NO pytest CI). Do NOT append `2>&1` to shell commands (the RTK hook parse-errors on `&`).
- **Bump `kj-controller/pyproject.toml` version** in the frontend task (cache-busts `app.js?v=`).
- **Deploy is later, off-show, with explicit permission** — this branch has a backend migration + new routes that need a `kj-controller` restart (interrupts playback). Do NOT push to main or restart the device as part of implementation.

---

## File Structure

- `kj-controller/stats_store.py` — MODIFY: add `_norm_artist`, `artist_norm` migration + backfill, and 9 read methods.
- `kj-controller/routes.py` — MODIFY: add 9 read-only `/stats/*` routes beside the existing ones (~line 4947).
- `kj-controller/tests/unit/test_stats_store.py` — MODIFY: unit tests for each new method + migration.
- `kj-controller/tests/unit/test_routes_stats.py` — MODIFY: endpoint tests for each new route.
- `kj-controller/templates/index.html` — MODIFY: remove `rotation-stats-btn` (line 88) + `#stats-modal` block (~1078); add `#song-stats` section after the Library `.container` (after ~line 401, before `<div class="container browser-mode-panel">`).
- `kj-controller/static/style.css` — MODIFY: add `.song-stats`, `.stats-overview`/`.stat-card`, `.stats-filters`, `.stats-seg`, `.stats-body`, `.stats-drill` rules.
- `kj-controller/static/app.js` — MODIFY: remove `openStatsModal`/`closeStatsModal`, rework `loadStats` into the new section's loaders; add the Song Stats module.
- `kj-controller/pyproject.toml` — MODIFY: version bump.

---

## Task 1: `artist_norm` migration + `_norm_artist` + `record_play` population

**Files:**
- Modify: `kj-controller/stats_store.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`

**Interfaces:**
- Produces: `_norm_artist(s) -> str` (module-level, whitespace-collapse + lowercase, `None`→`""`); `play_events.artist_norm` column (indexed); `record_play(...)` now also writes `artist_norm`.

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_stats_store.py`:

```python
from stats_store import _norm_artist  # add to the existing import line

def test_norm_artist():
    assert _norm_artist("  The   BEATLES ") == "the beatles"
    assert _norm_artist(None) == ""

def test_artist_norm_column_exists(store):
    cols = {r["name"] for r in store._get_conn().execute("PRAGMA table_info(play_events)")}
    assert "artist_norm" in cols

def test_record_play_populates_artist_norm(store):
    store.record_play("yt-a", entry_id=1, artist="The Beatles", title="Hey Jude",
                      song_key="the beatles hey jude", singer="Al")
    row = store._get_conn().execute(
        "SELECT artist_norm FROM play_events WHERE media_id='yt-a'").fetchone()
    assert row["artist_norm"] == "the beatles"

def test_artist_norm_backfilled_on_reopen(tmp_path):
    db = str(tmp_path / "m.db")
    s1 = StatsStore(db)
    # simulate a legacy row written before the column existed
    conn = s1._get_conn()
    conn.execute("UPDATE play_events SET artist_norm=NULL")  # no-op if empty
    s1.record_play("yt-b", entry_id=2, artist="ABBA", title="SOS", song_key="abba sos")
    conn.execute("UPDATE play_events SET artist_norm=NULL WHERE media_id='yt-b'")
    conn.commit()
    s2 = StatsStore(db)  # reopen → backfill runs
    row = s2._get_conn().execute(
        "SELECT artist_norm FROM play_events WHERE media_id='yt-b'").fetchone()
    assert row["artist_norm"] == "abba"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py -k "artist_norm or norm_artist" -v`
Expected: FAIL (`_norm_artist` import error / no column).

- [ ] **Step 3: Implement**

In `stats_store.py`, after the `_norm_singer` definition add:

```python
_norm_artist = _norm_singer  # same normalization: whitespace-collapse + lowercase
```

In `init_schema`, after `conn.commit()` inside the locked block is done, replace the method's tail so the additive migration runs in the lock and the backfill runs AFTER releasing it:

```python
    def init_schema(self):
        conn = self._get_conn()
        with self._lock():
            conn.executescript(
                # ... existing CREATE TABLE / CREATE INDEX script UNCHANGED ...
            )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(play_events)")}
            if "artist_norm" not in cols:
                conn.execute("ALTER TABLE play_events ADD COLUMN artist_norm TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_artist "
                         "ON play_events(artist_norm)")
            conn.commit()
        self._backfill_artist_norm()  # OUTSIDE the lock — never nest self._lock()

    def _backfill_artist_norm(self):
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                "SELECT id, artist FROM play_events "
                "WHERE artist_norm IS NULL AND artist IS NOT NULL AND artist <> ''"
            ).fetchall()
            for r in rows:
                conn.execute("UPDATE play_events SET artist_norm=? WHERE id=?",
                             (_norm_artist(r["artist"]), r["id"]))
            if rows:
                conn.commit()
```

In `record_play`, add `artist_norm` to the INSERT column list and its value:

```python
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO play_events
                (media_id, song_key, singer, singer_norm, played_at, night_date,
                 entry_id, source, artist, artist_norm, title)
                VALUES (?,?,?,?,
                        COALESCE(?, datetime('now')),
                        COALESCE(?, date('now','localtime')),
                        ?,?,?,?,?)
                """,
                (media_id, song_key, singer, _norm_singer(singer),
                 played_at, night_date, entry_id, source, artist,
                 _norm_artist(artist), title))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py -v`
Expected: PASS (including the pre-existing tests — the schema change is additive).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/tests/unit/test_stats_store.py
git commit -m "feat(stats): add artist_norm column + migration + record_play population"
```

---

## Task 2: `overview()` + `/stats/overview`

**Files:**
- Modify: `kj-controller/stats_store.py`, `kj-controller/routes.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`, `kj-controller/tests/unit/test_routes_stats.py`

**Interfaces:**
- Produces: `overview(*, since=None) -> {total_plays, distinct_songs, distinct_singers, distinct_artists, first_played, last_played, plays_last_30d}`; `GET /stats/overview?since=` → `{"overview": {...}}`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_stats_store.py`:

```python
def test_overview_counts(store):
    store.record_play("yt-a", entry_id=1, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-b", entry_id=2, singer="Bo", artist="Queen", title="One", song_key="queen one")
    store.record_play("yt-a", entry_id=3, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    o = store.overview()
    assert o["total_plays"] == 3
    assert o["distinct_songs"] == 2
    assert o["distinct_singers"] == 2
    assert o["distinct_artists"] == 2
    assert o["plays_last_30d"] == 3  # all just recorded

def test_overview_empty(store):
    o = store.overview()
    assert o["total_plays"] == 0 and o["distinct_songs"] == 0
```

`tests/unit/test_routes_stats.py`:

```python
def test_stats_overview_route(flask_test_client):
    flask_test_client.application.stats.record_play(
        "yt-a", entry_id=201, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    r = flask_test_client.get("/stats/overview")
    assert r.status_code == 200
    assert r.get_json()["overview"]["total_plays"] == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py::test_overview_counts tests/unit/test_routes_stats.py::test_stats_overview_route -v`
Expected: FAIL (`overview` not defined / 404).

- [ ] **Step 3: Implement**

`stats_store.py` (add method):

```python
    def overview(self, *, since=None):
        params = []
        where = ""
        if since:
            where = "WHERE played_at >= ?"
            params.append(since)
        conn = self._get_conn()
        with self._lock():
            row = conn.execute(
                f"""SELECT COUNT(*) total_plays,
                           COUNT(DISTINCT song_key) distinct_songs,
                           COUNT(DISTINCT NULLIF(singer_norm,'')) distinct_singers,
                           COUNT(DISTINCT NULLIF(artist_norm,'')) distinct_artists,
                           MIN(played_at) first_played, MAX(played_at) last_played
                    FROM play_events {where}""", params).fetchone()
            last30 = conn.execute(
                "SELECT COUNT(*) c FROM play_events "
                "WHERE played_at >= datetime('now','-30 days')").fetchone()["c"]
        d = dict(row)
        d["plays_last_30d"] = last30
        return d
```

`routes.py` (add after `stats_singers`):

```python
@routes_bp.route('/stats/overview', methods=['GET'])
def stats_overview():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"overview": {}})
    since = request.args.get('since') or None
    return jsonify({"overview": stats.overview(since=since)})
```

- [ ] **Step 4: Run to verify pass**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py tests/unit/test_routes_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/routes.py kj-controller/tests/unit/test_stats_store.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): overview() method + /stats/overview route"
```

---

## Task 3: `top_artists()` + `artist_songs()` + routes

**Files:**
- Modify: `kj-controller/stats_store.py`, `kj-controller/routes.py`
- Test: `kj-controller/tests/unit/test_stats_store.py`, `kj-controller/tests/unit/test_routes_stats.py`

**Interfaces:**
- Produces: `top_artists(*, since=None, limit=25) -> [{artist, plays, distinct_songs}]`; `artist_songs(artist, *, since=None, limit=100) -> [{song_key, artist, title, plays, distinct_singers}]`; `GET /stats/top-artists?since=&limit=` → `{"artists": [...]}`; `GET /stats/artist-songs?artist=&since=&limit=` → `{"songs": [...]}`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_stats_store.py`:

```python
def _seed_artist_rows(store):
    store.record_play("yt-a", entry_id=1, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-a", entry_id=2, singer="Bo", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-c", entry_id=3, singer="Al", artist="ABBA", title="Mamma", song_key="abba mamma")
    store.record_play("yt-d", entry_id=4, singer="Al", artist="Queen", title="One", song_key="queen one")

def test_top_artists(store):
    _seed_artist_rows(store)
    rows = store.top_artists()
    assert rows[0]["artist"] == "ABBA"
    assert rows[0]["plays"] == 3 and rows[0]["distinct_songs"] == 2

def test_artist_songs(store):
    _seed_artist_rows(store)
    rows = store.artist_songs("abba")
    keys = [r["song_key"] for r in rows]
    assert keys == ["abba sos", "abba mamma"]  # SOS(2) before Mamma(1)
    assert rows[0]["plays"] == 2 and rows[0]["distinct_singers"] == 2

def test_artist_songs_empty_artist(store):
    assert store.artist_songs("") == []
```

`tests/unit/test_routes_stats.py`:

```python
def test_stats_artist_routes(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=301, singer="Al", artist="ABBA", title="SOS", song_key="abba sos")
    r = flask_test_client.get("/stats/top-artists")
    assert r.get_json()["artists"][0]["artist"] == "ABBA"
    r2 = flask_test_client.get("/stats/artist-songs?artist=ABBA")
    assert r2.get_json()["songs"][0]["song_key"] == "abba sos"
```

- [ ] **Step 2: Run to verify fail**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py -k "artist" tests/unit/test_routes_stats.py::test_stats_artist_routes -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`stats_store.py`:

```python
    def top_artists(self, *, since=None, limit=25):
        clauses = ["artist_norm IS NOT NULL AND artist_norm <> ''"]
        params = []
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT MAX(artist) artist, COUNT(*) plays,
                           COUNT(DISTINCT song_key) distinct_songs
                    FROM play_events WHERE {where}
                    GROUP BY artist_norm ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def artist_songs(self, artist, *, since=None, limit=100):
        an = _norm_artist(artist)
        if not an:
            return []
        clauses = ["artist_norm=?", "song_key IS NOT NULL AND song_key <> ''"]
        params = [an]
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT song_key, MAX(artist) artist, MAX(title) title,
                           COUNT(*) plays, COUNT(DISTINCT NULLIF(singer_norm,'')) distinct_singers
                    FROM play_events WHERE {where}
                    GROUP BY song_key ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]
```

`routes.py`:

```python
@routes_bp.route('/stats/top-artists', methods=['GET'])
def stats_top_artists():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"artists": []})
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 25)), 100))
    except (TypeError, ValueError):
        limit = 25
    return jsonify({"artists": stats.top_artists(since=since, limit=limit)})


@routes_bp.route('/stats/artist-songs', methods=['GET'])
def stats_artist_songs():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"songs": []})
    artist = request.args.get('artist') or ''
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 200))
    except (TypeError, ValueError):
        limit = 100
    return jsonify({"songs": stats.artist_songs(artist, since=since, limit=limit)})
```

- [ ] **Step 4: Run to verify pass**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py tests/unit/test_routes_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/routes.py kj-controller/tests/unit/test_stats_store.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): top_artists + artist_songs methods + routes"
```

---

## Task 4: `singer_songs()` + `singer_song_history()` + routes

**Files:**
- Modify: `kj-controller/stats_store.py`, `kj-controller/routes.py`
- Test: both stats test files

**Interfaces:**
- Produces: `singer_songs(singer, *, since=None, limit=100) -> [{song_key, artist, title, plays, first_sung, last_sung}]`; `singer_song_history(singer, song_key, *, limit=200) -> [{played_at, night_date}]`; `GET /stats/singer-songs?singer=&since=&limit=` → `{"songs": [...]}`; `GET /stats/singer-song-history?singer=&song_key=&limit=` → `{"history": [...]}`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_stats_store.py`:

```python
def test_singer_songs(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-a", entry_id=2, singer="Celeste", artist="ABBA", title="SOS", song_key="abba sos")
    store.record_play("yt-c", entry_id=3, singer="Celeste", artist="Queen", title="One", song_key="queen one")
    rows = store.singer_songs("celeste")
    assert rows[0]["song_key"] == "abba sos" and rows[0]["plays"] == 2
    assert rows[0]["first_sung"] and rows[0]["last_sung"]

def test_singer_song_history(store):
    store.record_play("yt-a", entry_id=1, singer="Celeste", song_key="abba sos", night_date="2026-06-01")
    store.record_play("yt-a", entry_id=2, singer="Celeste", song_key="abba sos", night_date="2026-06-08")
    hist = store.singer_song_history("celeste", "abba sos")
    assert len(hist) == 2 and hist[0]["night_date"] in ("2026-06-01", "2026-06-08")

def test_singer_songs_empty(store):
    assert store.singer_songs("") == []
    assert store.singer_song_history("x", "") == []
```

`tests/unit/test_routes_stats.py`:

```python
def test_stats_singer_drilldown_routes(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=401, singer="Celeste", artist="ABBA", title="SOS",
                  song_key="abba sos", night_date="2026-06-01")
    r = flask_test_client.get("/stats/singer-songs?singer=Celeste")
    assert r.get_json()["songs"][0]["song_key"] == "abba sos"
    r2 = flask_test_client.get("/stats/singer-song-history?singer=Celeste&song_key=abba sos")
    assert r2.get_json()["history"][0]["night_date"] == "2026-06-01"
```

- [ ] **Step 2: Run to verify fail**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py -k "singer_song" tests/unit/test_routes_stats.py::test_stats_singer_drilldown_routes -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`stats_store.py`:

```python
    def singer_songs(self, singer, *, since=None, limit=100):
        sn = _norm_singer(singer)
        if not sn:
            return []
        clauses = ["singer_norm=?", "song_key IS NOT NULL AND song_key <> ''"]
        params = [sn]
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT song_key, MAX(artist) artist, MAX(title) title, COUNT(*) plays,
                           MIN(played_at) first_sung, MAX(played_at) last_sung
                    FROM play_events WHERE {where}
                    GROUP BY song_key ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def singer_song_history(self, singer, song_key, *, limit=200):
        sn = _norm_singer(singer)
        if not sn or not song_key:
            return []
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                """SELECT played_at, night_date FROM play_events
                   WHERE singer_norm=? AND song_key=?
                   ORDER BY played_at DESC LIMIT ?""",
                (sn, song_key, limit)).fetchall()
        return [dict(r) for r in rows]
```

`routes.py`:

```python
@routes_bp.route('/stats/singer-songs', methods=['GET'])
def stats_singer_songs():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"songs": []})
    singer = request.args.get('singer') or ''
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 200))
    except (TypeError, ValueError):
        limit = 100
    return jsonify({"songs": stats.singer_songs(singer, since=since, limit=limit)})


@routes_bp.route('/stats/singer-song-history', methods=['GET'])
def stats_singer_song_history():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"history": []})
    singer = request.args.get('singer') or ''
    song_key = request.args.get('song_key') or ''
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 500))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"history": stats.singer_song_history(singer, song_key, limit=limit)})
```

- [ ] **Step 4: Run to verify pass**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py tests/unit/test_routes_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/routes.py kj-controller/tests/unit/test_stats_store.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): singer_songs + singer_song_history methods + routes"
```

---

## Task 5: `song_history()` + `/stats/song-history`

**Files:**
- Modify: `kj-controller/stats_store.py`, `kj-controller/routes.py`
- Test: both stats test files

**Interfaces:**
- Produces: `song_history(song_key, *, since=None, limit=200) -> [{singer, played_at, night_date, media_id}]`; `GET /stats/song-history?song_key=&since=&limit=` → `{"history": [...]}`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_stats_store.py`:

```python
def test_song_history(store):
    store.record_play("yt-a", entry_id=1, singer="Al", song_key="abba sos", night_date="2026-06-01")
    store.record_play("yt-b", entry_id=2, singer="Bo", song_key="abba sos", night_date="2026-06-08")
    hist = store.song_history("abba sos")
    assert len(hist) == 2
    assert {h["singer"] for h in hist} == {"Al", "Bo"}
    assert "media_id" in hist[0]

def test_song_history_empty_key(store):
    assert store.song_history("") == []
```

`tests/unit/test_routes_stats.py`:

```python
def test_stats_song_history_route(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=501, singer="Al", song_key="abba sos", night_date="2026-06-01")
    r = flask_test_client.get("/stats/song-history?song_key=abba sos")
    assert r.get_json()["history"][0]["singer"] == "Al"
```

- [ ] **Step 2: Run to verify fail**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py -k song_history tests/unit/test_routes_stats.py::test_stats_song_history_route -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`stats_store.py`:

```python
    def song_history(self, song_key, *, since=None, limit=200):
        if not song_key:
            return []
        clauses = ["song_key=?"]
        params = [song_key]
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT singer, played_at, night_date, media_id
                    FROM play_events WHERE {where}
                    ORDER BY played_at DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]
```

`routes.py`:

```python
@routes_bp.route('/stats/song-history', methods=['GET'])
def stats_song_history():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"history": []})
    song_key = request.args.get('song_key') or ''
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 500))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"history": stats.song_history(song_key, since=since, limit=limit)})
```

- [ ] **Step 4: Run to verify pass**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py tests/unit/test_routes_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/routes.py kj-controller/tests/unit/test_stats_store.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): song_history method + /stats/song-history route"
```

---

## Task 6: `busiest_nights()` + `night_setlist()` + routes

**Files:**
- Modify: `kj-controller/stats_store.py`, `kj-controller/routes.py`
- Test: both stats test files

**Interfaces:**
- Produces: `busiest_nights(*, limit=20) -> [{night_date, plays, distinct_singers, distinct_songs}]`; `night_setlist(night_date, *, limit=200) -> [{played_at, singer, artist, title, song_key, media_id}]`; `GET /stats/nights?limit=` → `{"nights": [...]}`; `GET /stats/night-setlist?night_date=&limit=` → `{"setlist": [...]}`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_stats_store.py`:

```python
def test_busiest_nights(store):
    store.record_play("yt-a", entry_id=1, singer="Al", song_key="k1", night_date="2026-06-01")
    store.record_play("yt-b", entry_id=2, singer="Bo", song_key="k2", night_date="2026-06-01")
    store.record_play("yt-c", entry_id=3, singer="Al", song_key="k1", night_date="2026-06-08")
    rows = store.busiest_nights()
    assert rows[0]["night_date"] == "2026-06-01" and rows[0]["plays"] == 2
    assert rows[0]["distinct_singers"] == 2 and rows[0]["distinct_songs"] == 2

def test_night_setlist(store):
    store.record_play("yt-a", entry_id=1, singer="Al", artist="ABBA", title="SOS",
                      song_key="abba sos", night_date="2026-06-01")
    rows = store.night_setlist("2026-06-01")
    assert rows[0]["singer"] == "Al" and rows[0]["song_key"] == "abba sos"

def test_night_setlist_empty(store):
    assert store.night_setlist("") == []
```

`tests/unit/test_routes_stats.py`:

```python
def test_stats_nights_routes(flask_test_client):
    s = flask_test_client.application.stats
    s.record_play("yt-a", entry_id=601, singer="Al", artist="ABBA", title="SOS",
                  song_key="abba sos", night_date="2026-06-01")
    r = flask_test_client.get("/stats/nights")
    assert r.get_json()["nights"][0]["night_date"] == "2026-06-01"
    r2 = flask_test_client.get("/stats/night-setlist?night_date=2026-06-01")
    assert r2.get_json()["setlist"][0]["song_key"] == "abba sos"
```

- [ ] **Step 2: Run to verify fail**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py -k "night" tests/unit/test_routes_stats.py::test_stats_nights_routes -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`stats_store.py`:

```python
    def busiest_nights(self, *, limit=20):
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                """SELECT night_date, COUNT(*) plays,
                          COUNT(DISTINCT NULLIF(singer_norm,'')) distinct_singers,
                          COUNT(DISTINCT song_key) distinct_songs
                   FROM play_events WHERE night_date IS NOT NULL AND night_date <> ''
                   GROUP BY night_date ORDER BY plays DESC, night_date DESC LIMIT ?""",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def night_setlist(self, night_date, *, limit=200):
        if not night_date:
            return []
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                """SELECT played_at, singer, artist, title, song_key, media_id
                   FROM play_events WHERE night_date=?
                   ORDER BY played_at ASC LIMIT ?""",
                (night_date, limit)).fetchall()
        return [dict(r) for r in rows]
```

`routes.py`:

```python
@routes_bp.route('/stats/nights', methods=['GET'])
def stats_nights():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"nights": []})
    try:
        limit = max(1, min(int(request.args.get('limit', 20)), 100))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"nights": stats.busiest_nights(limit=limit)})


@routes_bp.route('/stats/night-setlist', methods=['GET'])
def stats_night_setlist():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"setlist": []})
    night_date = request.args.get('night_date') or ''
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 500))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"setlist": stats.night_setlist(night_date, limit=limit)})
```

- [ ] **Step 4: Run to verify pass**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py tests/unit/test_routes_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/routes.py kj-controller/tests/unit/test_stats_store.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): busiest_nights + night_setlist methods + routes"
```

---

## Task 7: `most_repeated()` + `/stats/most-repeated`

**Files:**
- Modify: `kj-controller/stats_store.py`, `kj-controller/routes.py`
- Test: both stats test files

**Interfaces:**
- Produces: `most_repeated(*, since=None, limit=10) -> [{singer, song_key, artist, title, plays}]` (singer+song combos with plays > 1, count desc); `GET /stats/most-repeated?since=&limit=` → `{"repeated": [...]}`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_stats_store.py`:

```python
def test_most_repeated(store):
    for eid in (1, 2, 3):
        store.record_play("yt-a", entry_id=eid, singer="Celeste", artist="Gaga",
                          title="Bad Romance", song_key="gaga bad romance")
    store.record_play("yt-b", entry_id=4, singer="Al", song_key="one off")
    rows = store.most_repeated()
    assert rows[0]["singer"] == "Celeste" and rows[0]["plays"] == 3
    assert all(r["plays"] > 1 for r in rows)  # one-offs excluded
```

`tests/unit/test_routes_stats.py`:

```python
def test_stats_most_repeated_route(flask_test_client):
    s = flask_test_client.application.stats
    for eid in (701, 702):
        s.record_play("yt-a", entry_id=eid, singer="Celeste", artist="Gaga",
                      title="Bad Romance", song_key="gaga bad romance")
    r = flask_test_client.get("/stats/most-repeated")
    assert r.get_json()["repeated"][0]["singer"] == "Celeste"
```

- [ ] **Step 2: Run to verify fail**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py::test_most_repeated tests/unit/test_routes_stats.py::test_stats_most_repeated_route -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`stats_store.py`:

```python
    def most_repeated(self, *, since=None, limit=10):
        clauses = ["singer_norm IS NOT NULL AND singer_norm <> ''",
                   "song_key IS NOT NULL AND song_key <> ''"]
        params = []
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT MAX(singer) singer, song_key, MAX(artist) artist,
                           MAX(title) title, COUNT(*) plays
                    FROM play_events WHERE {where}
                    GROUP BY singer_norm, song_key
                    HAVING plays > 1
                    ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]
```

`routes.py`:

```python
@routes_bp.route('/stats/most-repeated', methods=['GET'])
def stats_most_repeated():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"repeated": []})
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 10)), 50))
    except (TypeError, ValueError):
        limit = 10
    return jsonify({"repeated": stats.most_repeated(since=since, limit=limit)})
```

- [ ] **Step 4: Run to verify pass**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py tests/unit/test_routes_stats.py -v`
Expected: PASS (full stats suites green).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/stats_store.py kj-controller/routes.py kj-controller/tests/unit/test_stats_store.py kj-controller/tests/unit/test_routes_stats.py
git commit -m "feat(stats): most_repeated method + /stats/most-repeated route"
```

---

## Task 8: Remove modal, scaffold `#song-stats` section (markup + CSS + overview strip + lazy load)

**Files:**
- Modify: `kj-controller/templates/index.html`, `kj-controller/static/style.css`, `kj-controller/static/app.js`

**Interfaces:**
- Consumes: `/stats/overview`, `escHtml`.
- Produces: the `#song-stats` DOM, the `songStats` state object, `statsQS()`, `statsFetch()`, `renderStatsOverview()`, `initSongStats()` (IntersectionObserver lazy first load), and a `switchStatsView()` stub that Task 9 fills in for `top-songs`.

- [ ] **Step 1: Remove the modal**

In `templates/index.html`:
- Delete line 88 (`<button class="rotation-stats-btn" onclick="openStatsModal()" …>Song Stats</button>`).
- Delete the entire `#stats-modal` block (from `<div id="stats-modal" …>` through its matching `</div>`, ~lines 1078–1095).

In `static/app.js`:
- Delete `openStatsModal`, `closeStatsModal`, the old `loadStats` function, and the two trailing listeners `document.getElementById('statsRefresh')?.addEventListener(...)` and `document.getElementById('statsSinger')?.addEventListener(...)`.

- [ ] **Step 2: Add the section markup**

In `templates/index.html`, immediately AFTER the Library `.container available-songs` closing `</div>` (right before `<div class="container browser-mode-panel">`), insert:

```html
                <div class="container song-stats" id="song-stats">
                    <div class="header-row">
                        <h2>Song Stats</h2>
                        <div class="header-actions">
                            <button onclick="refreshSongStats()" title="Reload stats">Refresh</button>
                        </div>
                    </div>
                    <div id="statsOverview" class="stats-overview"></div>
                    <div class="stats-filters">
                        <div class="stats-range" id="statsRange" role="group" aria-label="Date range">
                            <button class="stats-range-btn stats-range-active" data-range="all">All time</button>
                            <button class="stats-range-btn" data-range="year">This year</button>
                            <button class="stats-range-btn" data-range="30d">Last 30 days</button>
                            <button class="stats-range-btn" data-range="custom">Custom</button>
                        </div>
                        <input type="date" id="statsSince" class="hidden" title="Custom start date">
                        <input type="text" id="statsSingerFilter" list="statsSingerList"
                               placeholder="Filter Top Songs by singer&hellip;" autocomplete="off">
                        <datalist id="statsSingerList"></datalist>
                    </div>
                    <div class="stats-seg" id="statsViewSwitch" role="group" aria-label="Stats view">
                        <button class="stats-seg-btn stats-seg-active" data-view="top-songs">Top Songs</button>
                        <button class="stats-seg-btn" data-view="top-singers">Top Singers</button>
                        <button class="stats-seg-btn" data-view="top-artists">Top Artists</button>
                        <button class="stats-seg-btn" data-view="nights">Nights</button>
                    </div>
                    <div id="statsBody" class="stats-body"></div>
                </div>
```

- [ ] **Step 3: Add CSS**

Append to `static/style.css` (dark theme tokens matching existing rules):

```css
/* ===== Song Stats section ===== */
.stats-overview { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 12px; }
.stat-card {
    background: #222; border: 1px solid #444; border-radius: 6px;
    padding: 6px 12px; min-width: 68px; text-align: center; flex: 0 0 auto;
}
.stat-card-val { font-size: 1.25em; font-weight: 600; color: #fff; }
.stat-card-label { font-size: 0.72em; color: #aaa; text-transform: uppercase; letter-spacing: 0.03em; }
.stats-filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
.stats-filters input[type="text"], .stats-filters input[type="date"] {
    background: #1a1a1a; border: 1px solid #444; color: #ddd; border-radius: 4px; padding: 4px 8px;
}
.stats-range { display: inline-flex; border: 1px solid #444; border-radius: 6px; overflow: hidden; }
.stats-range-btn, .stats-seg-btn {
    background: transparent; color: #bbb; border: 0; padding: 4px 12px; cursor: pointer;
    font-size: 0.8em; font-weight: 500;
}
.stats-range-btn + .stats-range-btn { border-left: 1px solid #444; }
.stats-range-btn:hover, .stats-seg-btn:hover { color: #ddd; background: rgba(255,255,255,0.04); }
.stats-range-active, .stats-seg-active { color: #fff; background: #3a3a3a; }
.stats-seg { display: inline-flex; border: 1px solid #444; border-radius: 6px; overflow: hidden; margin-bottom: 8px; }
.stats-seg-btn + .stats-seg-btn { border-left: 1px solid #444; }
.stats-body { max-height: 40vh; overflow-y: auto; }
.stats-row {
    display: flex; align-items: center; gap: 8px; padding: 6px 8px;
    border-bottom: 1px solid #2a2a2a; cursor: pointer;
}
.stats-row:hover { background: rgba(255,255,255,0.03); }
.stats-row-rank { color: #888; min-width: 1.6em; text-align: right; }
.stats-row-main { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stats-row-count { color: #ccc; flex: 0 0 auto; }
.stats-drill { padding: 4px 8px 8px 2.4em; background: #191919; border-bottom: 1px solid #2a2a2a; }
.stats-drill .stats-subrow { display: flex; gap: 8px; padding: 3px 0; color: #bbb; font-size: 0.85em; cursor: pointer; }
.stats-drill .stats-subrow:hover { color: #ddd; }
.stats-fun { color: #f5a; font-size: 0.85em; padding: 4px 8px; }
.stats-badge-busy { color: #f80; font-size: 0.8em; margin-left: 6px; }
.stats-muted { color: #777; padding: 8px; }
.stats-star { color: gold; }
```

- [ ] **Step 4: Add the JS core (state, fetch, overview, lazy init)**

Append to `static/app.js`:

```javascript
// ===== Song Stats section =====
const songStats = { view: 'top-songs', since: null, singer: '', cache: {}, loaded: false };

function statsQS(extra) {
    const p = new URLSearchParams();
    if (songStats.since) p.set('since', songStats.since);
    Object.entries(extra || {}).forEach(([k, v]) => { if (v) p.set(k, v); });
    const s = p.toString();
    return s ? '?' + s : '';
}

async function statsFetch(path) {
    try {
        const r = await fetch(path);
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
    } catch (e) {
        return null;  // graceful — caller shows empty/error state
    }
}

function statsDate10(ts) { return (ts || '').slice(0, 10); }

async function renderStatsOverview() {
    const data = await statsFetch('/stats/overview' + statsQS());
    const o = (data && data.overview) || {};
    const span = (o.first_played && o.last_played)
        ? statsDate10(o.first_played) + ' – ' + statsDate10(o.last_played) : '—';
    const cards = [
        ['Plays', o.total_plays || 0], ['Songs', o.distinct_songs || 0],
        ['Singers', o.distinct_singers || 0], ['Artists', o.distinct_artists || 0],
        ['Span', span], ['Last 30d', o.plays_last_30d || 0],
    ];
    document.getElementById('statsOverview').innerHTML = cards.map(([label, val]) =>
        '<div class="stat-card"><div class="stat-card-val">' + escHtml(String(val)) +
        '</div><div class="stat-card-label">' + escHtml(label) + '</div></div>').join('');
}

// switchStatsView is completed per-view in later tasks; top-songs is Task 9.
async function switchStatsView(view) {
    songStats.view = view;
    document.querySelectorAll('#statsViewSwitch .stats-seg-btn').forEach(b =>
        b.classList.toggle('stats-seg-active', b.dataset.view === view));
    const body = document.getElementById('statsBody');
    body.innerHTML = '<div class="stats-muted">Loading…</div>';
    // Filled in by later tasks:
    if (view === 'top-songs') return renderTopSongs();
    if (view === 'top-singers') return renderTopSingers();
    if (view === 'top-artists') return renderTopArtists();
    if (view === 'nights') return renderNights();
}

async function refreshSongStats() {
    songStats.cache = {};
    await renderStatsOverview();
    await switchStatsView(songStats.view);
}

function initSongStats() {
    const section = document.getElementById('song-stats');
    if (!section) return;
    // View switcher clicks
    document.getElementById('statsViewSwitch').addEventListener('click', (e) => {
        const btn = e.target.closest('.stats-seg-btn');
        if (btn) switchStatsView(btn.dataset.view);
    });
    // Lazy first load when the section scrolls into view
    const io = new IntersectionObserver((entries) => {
        if (entries.some(en => en.isIntersecting) && !songStats.loaded) {
            songStats.loaded = true;
            io.disconnect();
            renderStatsOverview();
            switchStatsView(songStats.view);
        }
    }, { rootMargin: '200px' });
    io.observe(section);
}

document.addEventListener('DOMContentLoaded', initSongStats);
```

Note: `renderTopSongs`/`renderTopSingers`/`renderTopArtists`/`renderNights` are defined in Tasks 9–12. To keep `node --check` and the harness green after THIS task, add temporary no-op stubs right below the block:

```javascript
async function renderTopSongs() { document.getElementById('statsBody').innerHTML = '<div class="stats-muted">Top Songs — coming next.</div>'; }
async function renderTopSingers() { document.getElementById('statsBody').innerHTML = '<div class="stats-muted">Top Singers — coming next.</div>'; }
async function renderTopArtists() { document.getElementById('statsBody').innerHTML = '<div class="stats-muted">Top Artists — coming next.</div>'; }
async function renderNights() { document.getElementById('statsBody').innerHTML = '<div class="stats-muted">Nights — coming next.</div>'; }
```

(Each later task REPLACES its stub with the real renderer.)

- [ ] **Step 5: Validate**

Run: `node --check kj-controller/static/app.js`
Expected: no output (valid).

Then render in the mock harness (see Task 13 for the harness script; a minimal version is fine here) and confirm: the Song Stats section appears below Library, overview cards populate from stubbed `/stats/overview`, the view switcher highlights the clicked pill, and no console errors.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/templates/index.html kj-controller/static/style.css kj-controller/static/app.js
git commit -m "feat(stats-ui): remove modal, scaffold #song-stats section + overview + lazy load"
```

---

## Task 9: Top Songs view + filters (range presets + singer filter) + song-history drill-down

**Files:**
- Modify: `kj-controller/static/app.js`

**Interfaces:**
- Consumes: `/stats/top-songs`, `/stats/song-history`, `/stats/singer-songs`, `/stats/singer-song-history`, `/stats/artist-songs`, `/stats/night-setlist`, `/stats/singers` (datalist), `songStats`, `statsQS`, `statsFetch`, `escHtml`, `escAttr`.
- Produces: real `renderTopSongs()`; the unified `toggleDrill(anchorEl, ds)` dispatcher (handles ALL drill kinds — `song`/`singer`/`singersong`/`artist`/`night` — so later view tasks only emit rows, no new handlers); the delegated `#statsBody` click listener; range-preset + singer-filter wiring; `populateSingerDatalist()`.

**Escaping model (applies to all view tasks 9–12):** rows/subrows carry their drill target in **`data-*` attributes** (`data-drill`, `data-key`, `data-singer`, `data-song-key`, `data-artist`, `data-night`), double-quoted and passed through `escAttr` (escapes `"`). A single delegated listener on `#statsBody` reads `element.dataset` and dispatches. **No inline `onclick` with interpolated user strings** — `escAttr` does NOT escape `'`, so an apostrophe ("O'Brien", "Guns N' Roses", a `song_key` with `'`) would break an inline JS arg. Delegation + double-quoted data attrs is apostrophe-safe and needs no `stopPropagation` (drills are inserted as SIBLINGS after the anchor, so a subrow click is never inside a `.stats-row`; the subrow selector is checked first).

- [ ] **Step 1: Replace the `renderTopSongs` stub**

```javascript
async function renderTopSongs() {
    const key = 'top-songs|' + (songStats.since || '') + '|' + songStats.singer;
    let songs = songStats.cache[key];
    if (!songs) {
        const data = await statsFetch('/stats/top-songs' + statsQS({ singer: songStats.singer, limit: 25 }));
        songs = (data && data.songs) || [];
        songStats.cache[key] = songs;
    }
    const body = document.getElementById('statsBody');
    if (!songs.length) { body.innerHTML = '<div class="stats-muted">No plays recorded yet.</div>'; return; }
    body.innerHTML = songs.map((s, i) => {
        const label = (s.artist ? escHtml(s.artist) + ' – ' : '') + escHtml(s.title || s.song_key);
        return '<div class="stats-row" data-drill="song" data-key="' + escAttr(s.song_key) + '">' +
            '<span class="stats-row-rank">' + (i + 1) + '</span>' +
            '<span class="stats-row-main">' + label + '</span>' +
            '<span class="stats-row-count">▶ ' + (s.plays || 0) + '</span></div>';
    }).join('');
}
```

- [ ] **Step 2: Add the unified drill dispatcher (handles every drill kind)**

```javascript
// Toggle a .stats-drill sibling right after anchorEl. `ds` is the element's dataset.
// Drill kinds: song | singer | singersong | artist | night.
async function toggleDrill(anchorEl, ds) {
    const next = anchorEl.nextElementSibling;
    if (next && next.classList.contains('stats-drill')) { next.remove(); return; }
    const drill = document.createElement('div');
    drill.className = 'stats-drill';
    drill.innerHTML = '<div class="stats-muted">Loading&hellip;</div>';
    anchorEl.after(drill);
    let items = [], render = null;
    if (ds.drill === 'song') {
        const d = await statsFetch('/stats/song-history' + statsQS({ song_key: ds.key, limit: 200 }));
        items = (d && d.history) || [];
        render = h => '<div class="stats-subrow"><span>' + escHtml(h.singer || '—') + '</span>' +
            '<span>' + escHtml(statsDate10(h.played_at || h.night_date)) + '</span></div>';
    } else if (ds.drill === 'singer') {
        const d = await statsFetch('/stats/singer-songs?singer=' + encodeURIComponent(ds.singer) +
            (songStats.since ? '&since=' + encodeURIComponent(songStats.since) : '') + '&limit=100');
        items = (d && d.songs) || [];
        render = s => {
            const label = (s.artist ? escHtml(s.artist) + ' – ' : '') + escHtml(s.title || s.song_key);
            return '<div class="stats-subrow" data-drill="singersong" data-singer="' + escAttr(ds.singer) +
                '" data-song-key="' + escAttr(s.song_key) + '"><span>' + label + '</span>' +
                '<span>▶ ' + s.plays + '</span><span>' + escHtml(statsDate10(s.last_sung)) + '</span></div>';
        };
    } else if (ds.drill === 'singersong') {
        const d = await statsFetch('/stats/singer-song-history?singer=' + encodeURIComponent(ds.singer) +
            '&song_key=' + encodeURIComponent(ds.songKey));
        items = (d && d.history) || [];
        render = h => '<div class="stats-subrow"><span>' +
            escHtml(statsDate10(h.played_at || h.night_date)) + '</span></div>';
    } else if (ds.drill === 'artist') {
        const d = await statsFetch('/stats/artist-songs?artist=' + encodeURIComponent(ds.artist) +
            (songStats.since ? '&since=' + encodeURIComponent(songStats.since) : '') + '&limit=100');
        items = (d && d.songs) || [];
        render = s => '<div class="stats-subrow"><span>' + escHtml(s.title || s.song_key) + '</span>' +
            '<span>▶ ' + s.plays + '</span><span>' + (s.distinct_singers || 0) + ' singers</span></div>';
    } else if (ds.drill === 'night') {
        const d = await statsFetch('/stats/night-setlist?night_date=' + encodeURIComponent(ds.night) + '&limit=200');
        items = (d && d.setlist) || [];
        render = x => {
            const label = (x.artist ? escHtml(x.artist) + ' – ' : '') + escHtml(x.title || x.song_key || '');
            return '<div class="stats-subrow"><span>' + escHtml(x.singer || '—') + '</span>' +
                '<span>' + label + '</span></div>';
        };
    }
    drill.innerHTML = (items.length && render) ? items.map(render).join('')
        : '<div class="stats-muted">No detail.</div>';
}
```

- [ ] **Step 3: Wire the delegated drill listener + filters**

Add the delegated drill listener (once) inside `initSongStats`, alongside the other listeners:

```javascript
    document.getElementById('statsBody').addEventListener('click', (e) => {
        const sub = e.target.closest('.stats-subrow[data-drill]');
        if (sub) { toggleDrill(sub, sub.dataset); return; }
        const row = e.target.closest('.stats-row');
        if (row) { toggleDrill(row, row.dataset); }
    });
```

Then the filters:

```javascript
function applyStatsRange(range) {
    const since = document.getElementById('statsSince');
    if (range === 'all') { songStats.since = null; since.classList.add('hidden'); }
    else if (range === '30d') {
        const d = new Date(); d.setDate(d.getDate() - 30);
        songStats.since = d.toISOString().slice(0, 10); since.classList.add('hidden');
    } else if (range === 'year') {
        songStats.since = new Date().getFullYear() + '-01-01'; since.classList.add('hidden');
    } else if (range === 'custom') {
        since.classList.remove('hidden');
        songStats.since = since.value || null;
    }
    songStats.cache = {};
    renderStatsOverview();
    switchStatsView(songStats.view);
}

async function populateSingerDatalist() {
    const data = await statsFetch('/stats/singers' + statsQS({ limit: 200 }));
    const singers = (data && data.singers) || [];
    document.getElementById('statsSingerList').innerHTML =
        singers.map(s => '<option value="' + escAttr(s.singer) + '">').join('');
}
```

In `initSongStats`, after the view-switcher listener, add:

```javascript
    document.getElementById('statsRange').addEventListener('click', (e) => {
        const btn = e.target.closest('.stats-range-btn');
        if (!btn) return;
        document.querySelectorAll('#statsRange .stats-range-btn').forEach(b =>
            b.classList.toggle('stats-range-active', b === btn));
        applyStatsRange(btn.dataset.range);
    });
    document.getElementById('statsSince').addEventListener('change', () => {
        songStats.since = document.getElementById('statsSince').value || null;
        songStats.cache = {}; renderStatsOverview(); switchStatsView(songStats.view);
    });
    document.getElementById('statsSingerFilter').addEventListener('change', (e) => {
        songStats.singer = e.target.value.trim();
        if (songStats.view === 'top-songs') switchStatsView('top-songs');
    });
```

And in the IntersectionObserver first-load block, also call `populateSingerDatalist();`.

- [ ] **Step 4: Validate**

Run: `node --check kj-controller/static/app.js`
Expected: valid.

Mock harness: switch to Top Songs (default) → rows render from stubbed `/stats/top-songs`; click a row → drill expands with stubbed `/stats/song-history`; click again → collapses; pick a range preset → cards + list reload; type a singer → Top Songs re-filters. No console errors, `event`-based row onclick uses the correct `song_key`.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(stats-ui): Top Songs view + range/singer filters + song-history drill-down"
```

---

## Task 10: Top Singers view + most-repeated fun fact + variety

**Files:**
- Modify: `kj-controller/static/app.js`

**Interfaces:**
- Consumes: `/stats/singers`, `/stats/most-repeated`, `escHtml`, `escAttr`, `statsQS`, `statsDate10`. The singer → songs → dates drill is already handled by the unified `toggleDrill` from Task 9 (`data-drill="singer"` on the row → `data-drill="singersong"` on each song subrow); this task defines NO new handler.
- Produces: real `renderTopSingers()`.

- [ ] **Step 1: Replace the `renderTopSingers` stub**

```javascript
async function renderTopSingers() {
    const key = 'top-singers|' + (songStats.since || '');
    let payload = songStats.cache[key];
    if (!payload) {
        const [sData, rData] = await Promise.all([
            statsFetch('/stats/singers' + statsQS({ limit: 50 })),
            statsFetch('/stats/most-repeated' + statsQS({ limit: 1 })),
        ]);
        payload = { singers: (sData && sData.singers) || [], repeated: (rData && rData.repeated) || [] };
        songStats.cache[key] = payload;
    }
    const body = document.getElementById('statsBody');
    let html = '';
    if (payload.repeated.length) {
        const t = payload.repeated[0];
        const song = (t.artist ? escHtml(t.artist) + ' – ' : '') + escHtml(t.title || t.song_key);
        html += '<div class="stats-fun">🔁 Most repeated: ' + escHtml(t.singer) +
                ' × ' + song + ' (' + t.plays + '×)</div>';
    }
    if (!payload.singers.length) { body.innerHTML = html + '<div class="stats-muted">No plays recorded yet.</div>'; return; }
    html += payload.singers.map((s, i) => {
        const variety = s.plays ? (s.distinct_songs / s.plays) : 0;
        return '<div class="stats-row" data-drill="singer" data-singer="' + escAttr(s.singer) + '">' +
            '<span class="stats-row-rank">' + (i + 1) + '</span>' +
            '<span class="stats-row-main">' + escHtml(s.singer) + '</span>' +
            '<span class="stats-row-count">▶ ' + s.plays + ' · ' + s.distinct_songs +
            ' songs · variety ' + variety.toFixed(2) + '</span></div>';
    }).join('');
    body.innerHTML = html;
}
```

- [ ] **Step 2: Validate**

Run: `node --check kj-controller/static/app.js`
Expected: valid.

Mock harness: switch to Top Singers → the most-repeated fun-fact line + singer rows with a variety figure; click a singer → their songs expand (unified `toggleDrill` 'singer'); click a song within → its dates expand ('singersong'); the inner click does NOT collapse the singer drill (the delegated listener checks the `.stats-subrow[data-drill]` selector first, and drills are siblings — so a subrow is never inside the `.stats-row`).

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(stats-ui): Top Singers view + most-repeated + variety"
```

---

## Task 11: Top Artists view

**Files:**
- Modify: `kj-controller/static/app.js`

**Interfaces:**
- Consumes: `/stats/top-artists`, `escHtml`, `escAttr`, `statsQS`. The artist → songs drill is handled by the unified `toggleDrill` 'artist' from Task 9 (`data-drill="artist"`); this task defines NO new handler.
- Produces: real `renderTopArtists()`.

- [ ] **Step 1: Replace the `renderTopArtists` stub**

```javascript
async function renderTopArtists() {
    const key = 'top-artists|' + (songStats.since || '');
    let artists = songStats.cache[key];
    if (!artists) {
        const data = await statsFetch('/stats/top-artists' + statsQS({ limit: 50 }));
        artists = (data && data.artists) || [];
        songStats.cache[key] = artists;
    }
    const body = document.getElementById('statsBody');
    if (!artists.length) { body.innerHTML = '<div class="stats-muted">No plays recorded yet.</div>'; return; }
    body.innerHTML = artists.map((a, i) =>
        '<div class="stats-row" data-drill="artist" data-artist="' + escAttr(a.artist) + '">' +
        '<span class="stats-row-rank">' + (i + 1) + '</span>' +
        '<span class="stats-row-main">' + escHtml(a.artist) + '</span>' +
        '<span class="stats-row-count">▶ ' + a.plays + ' · ' + a.distinct_songs + ' songs</span></div>'
    ).join('');
}
```

- [ ] **Step 2: Validate**

Run: `node --check kj-controller/static/app.js`
Expected: valid.

Mock harness: Top Artists → rows; click an artist → their songs expand (unified `toggleDrill` 'artist', showing title · plays · distinct singers); collapse on re-click.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(stats-ui): Top Artists view + artist drill-down"
```

---

## Task 12: Nights view + busiest badge

**Files:**
- Modify: `kj-controller/static/app.js`

**Interfaces:**
- Consumes: `/stats/nights`, `escHtml`, `escAttr`. The night → setlist drill is handled by the unified `toggleDrill` 'night' from Task 9 (`data-drill="night"`); this task defines NO new handler.
- Produces: real `renderNights()`.

- [ ] **Step 1: Replace the `renderNights` stub**

```javascript
async function renderNights() {
    const key = 'nights';  // nights is whole-history by design — ignores the `since` filter
    let nights = songStats.cache[key];
    if (!nights) {
        const data = await statsFetch('/stats/nights?limit=50');
        nights = (data && data.nights) || [];
        songStats.cache[key] = nights;
    }
    const body = document.getElementById('statsBody');
    if (!nights.length) { body.innerHTML = '<div class="stats-muted">No plays recorded yet.</div>'; return; }
    body.innerHTML = nights.map((n, i) =>
        '<div class="stats-row" data-drill="night" data-night="' + escAttr(n.night_date) + '">' +
        '<span class="stats-row-rank">' + (i + 1) + '</span>' +
        '<span class="stats-row-main">' + escHtml(n.night_date) +
        (i === 0 ? '<span class="stats-badge-busy">🔥 busiest</span>' : '') + '</span>' +
        '<span class="stats-row-count">▶ ' + n.plays + ' · ' + n.distinct_singers +
        ' singers · ' + n.distinct_songs + ' songs</span></div>'
    ).join('');
}
```

- [ ] **Step 2: Validate**

Run: `node --check kj-controller/static/app.js`
Expected: valid.

Mock harness: Nights → rows with the 🔥 busiest badge on row 1; click a night → that night's setlist expands (unified `toggleDrill` 'night', singer + song per line).

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(stats-ui): Nights view + night-setlist drill-down + busiest badge"
```

---

## Task 13: Version bump + full mock-harness Playwright validation

**Files:**
- Modify: `kj-controller/pyproject.toml`
- Create (scratch, not committed): a local mock-Flask harness under the scratchpad.

**Interfaces:**
- Consumes: all `/stats/*` endpoints (stubbed with fixtures), the real template + static assets.

- [ ] **Step 1: Bump the version**

In `kj-controller/pyproject.toml`, bump the `version = "…"` line (patch or minor — e.g. `0.58.x` → next). This cache-busts `app.js?v=`.

- [ ] **Step 2: Build the mock harness**

Create a scratch Flask app that renders the REAL `templates/index.html` + serves the REAL `static/`, and stubs every `/stats/*` endpoint with representative fixture JSON (multiple songs/singers/artists/nights, one most-repeated combo, a multi-day span, singers with varied `distinct_songs`/`plays`). Follow the pattern used by prior kjbox frontend PRs (Simple-Mode header-framework work). Save under the scratchpad dir, not the repo.

- [ ] **Step 3: Drive it with Playwright and verify**

Using a Playwright MCP browser, load the harness and confirm end-to-end:
- Song Stats section renders below Library; overview cards populate.
- Lazy load: `/stats/overview` + `/stats/top-songs` fire only when the section scrolls into view (not on initial page load) — assert via the stub's request log.
- View switcher: each of Top Songs / Top Singers / Top Artists / Nights renders its list; switching to an already-loaded view uses cache (no refetch).
- Drill-downs: Top Songs → song-history; Top Singers → songs → dates (inner click does not collapse outer via `stopPropagation`); Top Artists → artist-songs; Nights → setlist; each toggles closed on re-click.
- Filters: range presets reload overview + current view; the singer filter narrows Top Songs only.
- Fun stats: most-repeated line on Top Singers; 🔥 busiest badge on Nights row 1; variety figure on singer rows.
- The `.stats-body` list scrolls internally at ~40vh; the section is not excessively tall.
- No console errors; all displayed catalog/user strings are escaped (inject a fixture with `<`, `&`, `"`, `'` in artist/title/singer and confirm no broken markup).

- [ ] **Step 4: Final full backend suite**

Run: `rtk proxy python -m pytest tests/unit/test_stats_store.py tests/unit/test_routes_stats.py -v`
Expected: PASS. Then run the whole unit suite to confirm nothing else broke: `rtk proxy python -m pytest tests/unit -q`.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/pyproject.toml
git commit -m "chore(stats-ui): bump version for app.js cache-bust"
```

---

## Post-plan (outside the task loop)

1. `coderabbit review --agent --type committed --base origin/main` → fix real issues (max 3 cycles; skip pure nitpicks). Do NOT append `2>&1`.
2. `/pr` (adds `@coderabbitai ignore`). PR body links this plan + the design doc; notes the backend migration requires an off-show restart.
3. Merge + off-show deploy WITH Andrew's explicit permission; then verify `/stats/*` against the device's real rows and eyeball the section in the browser.

## Self-review notes (author)

- **Spec coverage:** every spec method/route/view/drill-down/overview-card/fun-stat maps to a task (T1 migration; T2 overview; T3 artists+artist-songs; T4 singer drill pair; T5 song-history; T6 nights pair; T7 most-repeated; T8 scaffold+overview+lazy; T9 Top Songs+filters+song drill; T10 Top Singers+variety+most-repeated+singer drill; T11 artists UI; T12 nights UI; T13 version+validation).
- **Type consistency:** route JSON keys are fixed per endpoint (`overview`/`artists`/`songs`/`history`/`nights`/`setlist`/`repeated`); frontend readers match them. All drills go through ONE `toggleDrill(anchorEl, ds)` dispatcher (Task 9), dispatching on `ds.drill` ∈ {`song`,`singer`,`singersong`,`artist`,`night`}; rows/subrows carry `data-*` attributes and a single delegated `#statsBody` listener routes clicks (no inline `onclick`, no per-view toggle functions).
- **Escaping safety:** every user/catalog string goes through `escHtml` (content) or `escAttr` (double-quoted `data-*` attributes). No user value is ever interpolated into an inline JS-string `onclick` arg — that removes the earlier `split('')` / apostrophe hazard.
- **`since` scope:** overview + Top Songs + Top Singers + Top Artists honor `since`; Nights uses whole history by design (documented in T12).
