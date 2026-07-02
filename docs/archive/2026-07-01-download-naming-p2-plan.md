# Download Naming — Phase 2 (LLM parsing + download-flow renaming + dedup-skip) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every *new* download a canonical `Artist - Title` (LLM-refined, esp. fixing artist/title order) written as a slug filename into a per-source subfolder with its stable `media_id`, upserted into `media_library`; skip re-downloading files we already have; and provide an offline-tolerant batch script that upgrades the existing `needs_review` backlog — all reusing a new karaoke-gen batch parse endpoint.

**Architecture:** Two PRs. **PR-A (karaoke-gen, ship first):** a stateless `POST /api/parse-karaoke-titles` batch endpoint that turns messy karaoke filenames into `{artist, title, confidence}` using the same Vertex Gemini client the match-judge uses. **PR-B (kjbox, tolerant of the endpoint's absence):** `GenClient.parse_titles`; a pure `naming.merge_llm_result` that folds an LLM result into a deterministic identity behind a confidence gate; the three `MediaIndex` download methods refactored to compute source + `media_id`, call the LLM inline (best-effort), write the slug into `downloads/<source>/`, and upsert `media_library` (fixing the gen `divebar__` mislabel); a reusable dedup-skip helper wired into the four enqueue sites; and `scripts/refine_titles.py` for the backlog.

**Tech Stack:** Python 3, FastAPI + Pydantic + Vertex `google-genai` (gen); Flask + sqlite3 + `requests` + yt-dlp (kjbox); pytest.

## Global Constraints

- **Authoritative design:** `docs/archive/2026-06-30-download-naming-normalization-design.md` §3 (parsing pipeline), §4 (gen endpoint), §5 (download flow + dedup-skip). This plan implements **only Phase 2**.
- **`media_id` scheme (verbatim):** `yt-<11-char-video-id>` · `db-<brand>-<file_id>` (fallback `db-<brand>-<hash8>`) · `gen-<job_id[:8]>` · `nomad-<disc#>` · `up-<sha1(file)[:12]>`. At download time community uses the **real Drive `file_id`** (`db-<brand>-<file_id>`); the slug embeds the id so `scan()`'s `extract_media_id` round-trips it (no dual-id).
- **Format order:** `Artist - Title` everywhere. Slug = `Artist - Title [media_id].ext` via `naming.build_slug_filename` (already shipped).
- **Per-source subfolders (under `download_folder`):** `youtube/` `community/` `gen/` `uploads/`. `NOMAD-720p/` (masters) is untouched by this phase.
- **Locked decisions (confirmed):** (1) refine = standalone `scripts/refine_titles.py`, dry-run default, **DB-only** updates (no file renames — that's P4), offline-tolerant; (2) new downloads = inline best-effort LLM → write slug **once** (offline → deterministic name + `needs_review=1`); (3) dedup-skip at the four enqueue sites where `media_id` is cheap.
- **Offline resilience (mandatory — live-show device):** every gen call is wrapped try/except → falls back to the deterministic guess + `needs_review`. A missing/undeployed endpoint, a timeout, or a 4xx/5xx must never crash a download, block a link, or interrupt playback.
- **Confidence gate:** default threshold `0.75` (config `parse_confidence_threshold`); `confidence >= threshold` → `needs_review=0`, else `1`.
- **kjbox has no pytest CI** (only `security.yml`) — run `cd kj-controller && pytest` locally. New unit tests live in `kj-controller/tests/unit/`. **karaoke-gen** has full CI + `make test`; new tests live under `backend/tests/`.
- **Reuse, don't reinvent:** kjbox — `naming.build_slug_filename` / `media_id_for` / `parse_identity` / `extract_media_id` / `SOURCE_*`; `MediaLibraryStore`; `utils.parse_youtube_filename`. gen — the Vertex client pattern in `backend/services/match_judge/ai.py`, `require_admin`, `settings`.
- **Production safety:** kjbox autodeploy is OFF; backend changes need a manual off-show restart. gen deploys on merge to main (Cloud Run).

---

# PR-A — karaoke-gen: `POST /api/parse-karaoke-titles`

Ship this PR first. kjbox (PR-B) treats its absence as "offline".

## File Structure (PR-A)

| File | Responsibility |
|------|----------------|
| `backend/services/parse_titles/__init__.py` (new) | Package marker; re-export `parse_titles`. |
| `backend/services/parse_titles/service.py` (new) | `async parse_titles(items, *, model=None, generate=None) -> list[dict]`: batches items into one Gemini call, validates/repairs the response, always returns one result per input id (kill-switch + failure → empty-ish results). |
| `backend/services/parse_titles/ai.py` (new) | Prompt construction + Vertex `genai` call (`_blocking_generate`), mirroring `match_judge/ai.py`. Response schema = array of `{id, artist, title, confidence}`. |
| `backend/api/routes/parse_titles.py` (new) | `router` with `POST /parse-karaoke-titles`; Pydantic request/response models; `require_admin`. |
| `backend/main.py` (modify: import list ~L13, include ~after L181) | Register the router with `prefix="/api"`. |
| `backend/config.py` (modify: after match_judge keys ~L110-118) | `parse_titles_enabled`, `parse_titles_model`, `parse_titles_timeout_ms`, `parse_titles_max_items`. |
| `backend/tests/test_parse_titles.py` (new) | Unit tests: prompt/parse mapping, batch id-alignment, kill-switch, injected-generate happy path, malformed-response degrade. |
| `backend/tests/test_parse_titles_route.py` (new) | Route test: admin auth required; happy path via dependency override + injected service. |

---

### Task A1: Settings keys for the parse endpoint

**Files:**
- Modify: `backend/config.py` (in `class Settings`, after the `match_judge_timeout_ms` line ~118)
- Test: `backend/tests/test_parse_titles.py` (create with this one test first)

**Interfaces:**
- Produces: `settings.parse_titles_enabled: bool`, `settings.parse_titles_model: str`, `settings.parse_titles_timeout_ms: int`, `settings.parse_titles_max_items: int` — consumed by A2/A3.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_parse_titles.py
def test_settings_parse_titles_defaults():
    from backend.config import Settings
    s = Settings()
    assert s.parse_titles_enabled is True
    assert s.parse_titles_model == "gemini-3.5-flash"
    assert s.parse_titles_timeout_ms == 20000
    assert s.parse_titles_max_items == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen && python -m pytest backend/tests/test_parse_titles.py::test_settings_parse_titles_defaults -q`
Expected: FAIL (`AttributeError: parse_titles_enabled`).

- [ ] **Step 3: Add the settings**

In `backend/config.py`, after the `match_judge_timeout_ms` line:

```python
    # Batch karaoke-filename parser (kjbox download-naming). Reuses the Vertex
    # Gemini stack. Larger timeout than match_judge because it batches ~200 items.
    parse_titles_enabled: bool = os.getenv("PARSE_TITLES_ENABLED", "true").lower() in (
        "1", "true", "yes",
    )
    parse_titles_model: str = os.getenv("PARSE_TITLES_MODEL", "gemini-3.5-flash")
    parse_titles_timeout_ms: int = int(os.getenv("PARSE_TITLES_TIMEOUT_MS", "20000"))
    parse_titles_max_items: int = int(os.getenv("PARSE_TITLES_MAX_ITEMS", "200"))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_parse_titles.py
git commit -m "feat(config): parse-titles endpoint settings"
```

---

### Task A2: `parse_titles/ai.py` — prompt + Vertex call

**Files:**
- Create: `backend/services/parse_titles/__init__.py`, `backend/services/parse_titles/ai.py`
- Test: `backend/tests/test_parse_titles.py` (add cases)

**Interfaces:**
- Consumes: `backend.config.settings`.
- Produces:
  - `build_prompts(items: list[dict]) -> tuple[str, str]` (system, user).
  - `RESPONSE_SCHEMA` (dict) — object with `results` array of `{id, artist, title, confidence}`.
  - `parse_map_from_response(data, items) -> list[dict]` — id-aligned `[{id, artist, title, confidence}]`, one per input, filling misses with `{artist:"", title:"", confidence:0.0}`.
  - `async ai_parse(items, *, model=None, generate=None) -> list[dict]` (used by A3).
  - `_default_generate(model, system, user) -> dict` (async, real Vertex).

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_parse_titles.py  (append)
import asyncio
from backend.services.parse_titles import ai


def test_build_prompts_lists_items_with_ids():
    system, user = ai.build_prompts(
        [{"id": "a", "filename": "Santeria - Sublime _ Karaoke _ KaraFun.mp4",
          "channel": "KaraFun", "source": "youtube"}]
    )
    assert "artist" in system.lower() and "title" in system.lower()
    assert '"a"' in user or "id=a" in user or "a:" in user  # id must be echoed
    assert "Santeria" in user


def test_parse_map_from_response_aligns_by_id_and_fills_misses():
    items = [{"id": "a", "filename": "x"}, {"id": "b", "filename": "y"}]
    data = {"results": [{"id": "a", "artist": "Sublime", "title": "Santeria",
                         "confidence": 0.9}]}
    out = ai.parse_map_from_response(data, items)
    assert {r["id"] for r in out} == {"a", "b"}
    a = next(r for r in out if r["id"] == "a")
    assert (a["artist"], a["title"]) == ("Sublime", "Santeria")
    b = next(r for r in out if r["id"] == "b")
    assert b["artist"] == "" and b["title"] == "" and b["confidence"] == 0.0


def test_ai_parse_uses_injected_generate():
    items = [{"id": "a", "filename": "Bella Kay - iloveit (Karaoke Version).mp4"}]

    async def fake_generate(model, system, user):
        return {"results": [{"id": "a", "artist": "Bella Kay",
                             "title": "iloveit", "confidence": 0.82}]}

    out = asyncio.run(ai.ai_parse(items, generate=fake_generate))
    assert out[0]["artist"] == "Bella Kay" and out[0]["confidence"] == 0.82


def test_parse_map_handles_garbage_response():
    items = [{"id": "a", "filename": "x"}]
    assert ai.parse_map_from_response("not a dict", items) == [
        {"id": "a", "artist": "", "title": "", "confidence": 0.0}
    ]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles.py -q`
Expected: FAIL (`ModuleNotFoundError: backend.services.parse_titles`).

- [ ] **Step 3: Implement the package**

`backend/services/parse_titles/__init__.py`:

```python
"""Batch karaoke-filename → {artist, title, confidence} parser (Vertex Gemini)."""
from backend.services.parse_titles.service import parse_titles  # noqa: F401
```

`backend/services/parse_titles/ai.py`:

```python
"""Vertex-Gemini layer for batch karaoke-filename parsing.

Turns messy karaoke *filenames* (YouTube titles, community/producer naming,
KaraFun-reversed order) into canonical {artist, title, confidence}. Distinct
from match_judge (which judges an already-split artist/title). The model call
is injectable for tests.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

Generate = Callable[[str, str, str], Awaitable[dict]]

_SYSTEM_PROMPT = (
    "You extract canonical song metadata from karaoke video/file names for a "
    "karaoke library. Each item has an id and a filename (sometimes a channel or "
    "source). Filenames are noisy: they carry karaoke markers ('(Karaoke Version)', "
    "'KARAOKE', '[karaoke]', 'Instrumental', producer/brand tags), YouTube ids or "
    "channel names, and inconsistent separators. Crucially the artist/title ORDER "
    "is inconsistent — most are 'Artist - Title' but some sources (e.g. KaraFun) are "
    "'Title - Artist'. Use your knowledge of real songs to put artist and title in "
    "the correct fields.\n"
    "Return JSON: {\"results\": [{\"id\", \"artist\", \"title\", \"confidence\"}]}. "
    "Return exactly one result per input id, echoing the id verbatim.\n"
    "artist/title: official formatting, karaoke noise removed, no brand codes or "
    "YouTube ids. If you cannot identify one field, return it as an empty string.\n"
    "confidence: 0.0-1.0 — your certainty the artist/title (and their order) are "
    "correct. Be honest; low confidence for ambiguous or unknown songs. Never invent "
    "a song; if unsure, return best-effort split with low confidence."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["id"],
            },
        }
    },
    "required": ["results"],
}


def _model() -> str:
    try:
        from backend.config import settings
        return getattr(settings, "parse_titles_model", "gemini-3.5-flash")
    except Exception:  # pragma: no cover - config guard
        return "gemini-3.5-flash"


def build_prompts(items: list[dict]) -> tuple[str, str]:
    lines = ["Parse these karaoke filenames:"]
    for it in items:
        parts = [f'id={it.get("id")!r}', f'filename={it.get("filename", "")!r}']
        if it.get("channel"):
            parts.append(f'channel={it["channel"]!r}')
        if it.get("source"):
            parts.append(f'source={it["source"]!r}')
        lines.append("- " + " ".join(parts))
    return _SYSTEM_PROMPT, "\n".join(lines)


def _clean(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ""


def _conf(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def parse_map_from_response(data: object, items: list[dict]) -> list[dict]:
    """Return one id-aligned result per input, filling any misses with blanks."""
    by_id: dict[str, dict] = {}
    if isinstance(data, dict):
        for r in data.get("results") or []:
            if isinstance(r, dict) and r.get("id") is not None:
                by_id[str(r["id"])] = r
    out = []
    for it in items:
        rid = str(it.get("id"))
        r = by_id.get(rid, {})
        out.append({
            "id": rid,
            "artist": _clean(r.get("artist")),
            "title": _clean(r.get("title")),
            "confidence": _conf(r.get("confidence")),
        })
    return out


async def ai_parse(
    items: list[dict], *, model: Optional[str] = None,
    generate: Optional[Generate] = None,
) -> list[dict]:
    gen = generate or _default_generate
    system, user = build_prompts(items)
    data = await gen(model or _model(), system, user)
    return parse_map_from_response(data, items)


async def _default_generate(model: str, system: str, user: str) -> dict:
    return await asyncio.to_thread(_blocking_generate, model, system, user)


def _blocking_generate(model: str, system: str, user: str) -> dict:
    from google import genai
    from google.genai import types

    from backend.config import settings

    timeout_ms = int(getattr(settings, "parse_titles_timeout_ms", 20000))
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location="global",
        http_options=types.HttpOptions(timeout=timeout_ms),
    )
    response = client.models.generate_content(
        model=model,
        contents=[user],
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=copy.deepcopy(RESPONSE_SCHEMA),
            temperature=0,
        ),
    )
    return json.loads(response.text)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/parse_titles/ backend/tests/test_parse_titles.py
git commit -m "feat(parse-titles): Vertex Gemini batch filename parser"
```

---

### Task A3: `parse_titles/service.py` — batching + kill-switch + degrade

**Files:**
- Create: `backend/services/parse_titles/service.py`
- Test: `backend/tests/test_parse_titles.py` (add cases)

**Interfaces:**
- Consumes: `ai.ai_parse`, `settings.parse_titles_enabled`, `settings.parse_titles_max_items`.
- Produces: `async parse_titles(items: list[dict], *, model=None, generate=None) -> list[dict]` — one id-aligned result per input; kill-switch OFF or any exception → blanks (`confidence=0.0`) so the caller falls back to deterministic; caps at `parse_titles_max_items` (extras returned as blanks).

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_parse_titles.py  (append)
from backend.services.parse_titles import service


def test_parse_titles_disabled_returns_blanks(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", False)
    out = asyncio.run(service.parse_titles([{"id": "a", "filename": "x"}]))
    assert out == [{"id": "a", "artist": "", "title": "", "confidence": 0.0}]


def test_parse_titles_degrades_on_generate_error(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)

    async def boom(model, system, user):
        raise RuntimeError("vertex down")

    out = asyncio.run(service.parse_titles(
        [{"id": "a", "filename": "x"}], generate=boom))
    assert out == [{"id": "a", "artist": "", "title": "", "confidence": 0.0}]


def test_parse_titles_happy_path(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "parse_titles_enabled", True)

    async def gen(model, system, user):
        return {"results": [{"id": "a", "artist": "Queen",
                             "title": "Bohemian Rhapsody", "confidence": 0.95}]}

    out = asyncio.run(service.parse_titles([{"id": "a", "filename": "x"}], generate=gen))
    assert out[0]["artist"] == "Queen" and out[0]["confidence"] == 0.95
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'parse_titles'`).

- [ ] **Step 3: Implement the service**

```python
# backend/services/parse_titles/service.py
"""Batch orchestration + graceful degrade for karaoke-filename parsing."""
from __future__ import annotations

import logging
from typing import Optional

from backend.services.parse_titles import ai

logger = logging.getLogger(__name__)


def _blanks(items: list[dict]) -> list[dict]:
    return [
        {"id": str(it.get("id")), "artist": "", "title": "", "confidence": 0.0}
        for it in items
    ]


async def parse_titles(
    items: list[dict], *, model=None, generate=None
) -> list[dict]:
    """Parse a batch of filenames → id-aligned {id, artist, title, confidence}.

    Never raises: a disabled kill-switch or any model failure yields blank
    results so the (kjbox) caller keeps its deterministic guess.
    """
    if not items:
        return []
    try:
        from backend.config import settings
        enabled = getattr(settings, "parse_titles_enabled", True)
        max_items = int(getattr(settings, "parse_titles_max_items", 200))
    except Exception:  # pragma: no cover
        enabled, max_items = True, 200
    if not enabled:
        return _blanks(items)

    head, tail = items[:max_items], items[max_items:]
    try:
        results = await ai.ai_parse(head, model=model, generate=generate)
    except Exception as exc:
        logger.warning("parse_titles degraded (%s); returning blanks", exc)
        return _blanks(items)
    return results + _blanks(tail)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/parse_titles/service.py backend/tests/test_parse_titles.py
git commit -m "feat(parse-titles): batching, kill-switch, graceful degrade"
```

---

### Task A4: Route `POST /api/parse-karaoke-titles` + register it

**Files:**
- Create: `backend/api/routes/parse_titles.py`
- Modify: `backend/main.py` (import list ~L13; `include_router` block ~after L181)
- Test: `backend/tests/test_parse_titles_route.py`

**Interfaces:**
- Consumes: `service.parse_titles`, `require_admin`.
- Produces: `POST /api/parse-karaoke-titles` — request `{items:[{id, filename, channel?, source?}]}` → `{results:[{id, artist, title, confidence}]}`.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_parse_titles_route.py
from fastapi.testclient import TestClient


def _client_with_overrides(monkeypatch):
    from backend.main import app
    from backend.api.dependencies import require_admin
    from backend.services.auth_service import AuthResult
    from backend.api.routes import parse_titles as route

    async def fake_parse(items, **kw):
        return [{"id": str(it["id"]), "artist": "Queen",
                 "title": "Bohemian Rhapsody", "confidence": 0.9} for it in items]

    monkeypatch.setattr(route, "parse_titles", fake_parse)
    app.dependency_overrides[require_admin] = lambda: AuthResult(
        is_admin=True, user_type="admin", user_email="a@nomadkaraoke.com")
    return TestClient(app), app


def test_parse_route_happy_path(monkeypatch):
    client, app = _client_with_overrides(monkeypatch)
    try:
        resp = client.post("/api/parse-karaoke-titles", json={
            "items": [{"id": "1", "filename": "x.mp4", "source": "youtube"}]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"][0]["id"] == "1"
        assert body["results"][0]["artist"] == "Queen"
    finally:
        app.dependency_overrides.clear()


def test_parse_route_requires_admin():
    from backend.main import app
    client = TestClient(app)
    resp = client.post("/api/parse-karaoke-titles",
                       json={"items": [{"id": "1", "filename": "x"}]})
    assert resp.status_code in (401, 403)
```

(Confirm the `AuthResult(...)` kwargs against `backend/services/auth_service.py`; adjust to the real constructor if it differs — the point is an admin result.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles_route.py -q`
Expected: FAIL (404 — route not registered).

- [ ] **Step 3: Implement the route**

```python
# backend/api/routes/parse_titles.py
"""POST /api/parse-karaoke-titles — batch karaoke-filename → artist/title.

Internal admin endpoint used by kjbox to canonicalise downloaded-file names.
Reuses the Vertex Gemini parser; never blocks the caller (kjbox degrades to a
deterministic guess when this is unavailable).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult
from backend.services.parse_titles import parse_titles

logger = logging.getLogger(__name__)

router = APIRouter(tags=["parse-titles"])


class ParseItem(BaseModel):
    id: str
    filename: str
    channel: Optional[str] = None
    source: Optional[str] = None


class ParseRequest(BaseModel):
    items: list[ParseItem]


class ParseResult(BaseModel):
    id: str
    artist: str
    title: str
    confidence: float


class ParseResponse(BaseModel):
    results: list[ParseResult]


@router.post("/parse-karaoke-titles", response_model=ParseResponse)
async def parse_karaoke_titles(
    body: ParseRequest,
    auth_result: AuthResult = Depends(require_admin),
):
    items = [i.model_dump() for i in body.items]
    results = await parse_titles(items)
    logger.info("parse-karaoke-titles: %d items", len(items))
    return {"results": results}
```

- [ ] **Step 4: Register the router in `backend/main.py`**

Add `parse_titles` to the routes import on ~L13:

```python
from backend.api.routes import health, jobs, internal, file_upload, review, auth, audio_search, themes, users, admin, tenant, rate_limits, push, catalog, encoding_worker, client_errors, bulk, parse_titles
```

After the `catalog` include (near L181's block), add:

```python
app.include_router(parse_titles.router, prefix="/api")  # kjbox filename parser
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles_route.py backend/tests/test_parse_titles.py -q`
Expected: PASS.

- [ ] **Step 6: Bump version + commit**

Bump `tool.poetry.version` in `pyproject.toml` (patch/minor per repo convention).

```bash
git add backend/api/routes/parse_titles.py backend/main.py backend/tests/test_parse_titles_route.py pyproject.toml
git commit -m "feat(api): POST /api/parse-karaoke-titles (admin, kjbox filename parser)"
```

- [ ] **Step 7: Full backend regression**

Run: `cd karaoke-gen && python -m pytest backend/tests/test_parse_titles.py backend/tests/test_parse_titles_route.py -q` (and, before PR, `make test 2>&1 | tail -100`).
Expected: PASS.

---

# PR-B — kjbox: client, download renaming, dedup-skip, refine script

Depends on PR-A's contract but **must run without it** (offline → deterministic + `needs_review`).

## File Structure (PR-B)

| File | Responsibility |
|------|----------------|
| `kj-controller/gen_client.py` (modify) | `GenClient.parse_titles(items) -> list[dict] | None`. |
| `kj-controller/naming.py` (modify) | Pure `merge_llm_result(deterministic, llm, threshold) -> dict`; `youtube_id_from_url(url) -> str | None`. |
| `kj-controller/media_library.py` (modify) | `apply_parse(media_id, artist, title, confidence, threshold) -> bool`. |
| `kj-controller/config.py` (modify) | `parse_confidence_threshold` (0.75) default key. |
| `kj-controller/media.py` (modify) | Refactor `download_video` / `download_from_url` / `download_cdg_pair` to compute source+`media_id`, inline LLM refine, write slug into `downloads/<source>/`, upsert `media_library`; add `self.gen_client=None` + shared `_finalize_download_identity(...)`. |
| `kj-controller/app.py` (modify) | Inject `flask_app.media.gen_client` after `gen_client` is built (both factory paths). |
| `kj-controller/routes.py` (modify) | `_prospective_media_id(...)`, `_dedup_link_or_skip(...)`; thread `artist/title/brand_code/file_id` into `_resolve_divebar_spec` output; wire dedup-skip into the 4 enqueue sites; `_download_worker` passes identity to `download_from_url`/`download_cdg_pair`. |
| `kj-controller/scripts/refine_titles.py` (new) | Batch refine of `needs_review=1` rows via `GenClient.parse_titles`; dry-run default; DB-only. |
| `kj-controller/tests/unit/test_gen_client_parse.py` (new) | `parse_titles` happy path + offline `None`. |
| `kj-controller/tests/unit/test_naming_merge.py` (new) | `merge_llm_result` gate + `youtube_id_from_url`. |
| `kj-controller/tests/unit/test_media_library_apply_parse.py` (new) | `apply_parse` behavior. |
| `kj-controller/tests/unit/test_download_renaming.py` (new) | Download methods write slug into source folder + upsert. |
| `kj-controller/tests/unit/test_dedup_skip.py` (new) | Dedup-skip helper + enqueue integration. |
| `kj-controller/tests/unit/test_refine_titles.py` (new) | Refine script dry-run + execute + offline. |

---

### Task B1: `naming.merge_llm_result` + `youtube_id_from_url` (pure)

**Files:**
- Modify: `kj-controller/naming.py`
- Test: `kj-controller/tests/unit/test_naming_merge.py`

**Interfaces:**
- Produces (used by B4, B5, B6, B8):
  - `merge_llm_result(deterministic: dict, llm: dict | None, threshold: float) -> dict` — returns a new identity dict. If `llm` is falsy or has empty artist **and** title → return `deterministic` unchanged. Else set `artist`/`title` from llm, `confidence`=llm confidence, `parse_method="llm"`, `needs_review = 0 if confidence >= threshold else 1`. Preserves `source`/`source_ref` from `deterministic`.
  - `youtube_id_from_url(url: str) -> str | None` — 11-char id from `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`.

- [ ] **Step 1: Write failing tests**

```python
# kj-controller/tests/unit/test_naming_merge.py
import naming


def _det():
    return {"source": "youtube", "source_ref": "UM1XiyBmhM",
            "artist": "Bella Kay", "title": "iloveit",
            "confidence": 0.4, "needs_review": 1, "parse_method": "deterministic"}


def test_merge_none_keeps_deterministic():
    assert naming.merge_llm_result(_det(), None, 0.75) == _det()


def test_merge_empty_llm_keeps_deterministic():
    assert naming.merge_llm_result(_det(), {"artist": "", "title": "", "confidence": 0.0}, 0.75) == _det()


def test_merge_high_confidence_clears_review():
    out = naming.merge_llm_result(
        _det(), {"artist": "Bella Kay", "title": "iloveit", "confidence": 0.9}, 0.75)
    assert out["parse_method"] == "llm"
    assert out["needs_review"] == 0
    assert out["confidence"] == 0.9
    assert out["source_ref"] == "UM1XiyBmhM"  # identity preserved


def test_merge_low_confidence_keeps_review_but_takes_values():
    out = naming.merge_llm_result(
        _det(), {"artist": "Sublime", "title": "Santeria", "confidence": 0.5}, 0.75)
    assert (out["artist"], out["title"]) == ("Sublime", "Santeria")
    assert out["needs_review"] == 1
    assert out["parse_method"] == "llm"


def test_youtube_id_from_url():
    assert naming.youtube_id_from_url("https://www.youtube.com/watch?v=UM1XiyBmhM") == "UM1XiyBmhM"
    assert naming.youtube_id_from_url("https://youtu.be/UM1XiyBmhM?t=3") == "UM1XiyBmhM"
    assert naming.youtube_id_from_url("https://www.youtube.com/shorts/UM1XiyBmhM") == "UM1XiyBmhM"
    assert naming.youtube_id_from_url("not a url") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_naming_merge.py -v`
Expected: FAIL (`AttributeError: merge_llm_result`).

- [ ] **Step 3: Implement in `naming.py`** (append; add `from urllib.parse import urlparse, parse_qs` at top)

```python
_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def youtube_id_from_url(url):
    """Extract an 11-char YouTube video id from a watch/youtu.be/shorts/embed URL."""
    if not url:
        return None
    try:
        u = urlparse(url)
    except (ValueError, TypeError):
        return None
    host = (u.hostname or "").lower()
    if host.endswith("youtu.be"):
        cand = u.path.lstrip("/").split("/")[0]
        return cand if _YT_ID_RE.fullmatch(cand) else None
    if "youtube" in host:
        qs = parse_qs(u.query or "")
        if qs.get("v") and _YT_ID_RE.fullmatch(qs["v"][0]):
            return qs["v"][0]
        for seg in ("shorts", "embed"):
            marker = f"/{seg}/"
            if marker in u.path:
                cand = u.path.split(marker, 1)[1].split("/")[0]
                return cand if _YT_ID_RE.fullmatch(cand) else None
    return None


def merge_llm_result(deterministic, llm, threshold):
    """Fold an LLM parse result into a deterministic identity behind a gate.

    llm falsy or empty (no artist and no title) -> return deterministic as-is.
    Otherwise take the LLM's artist/title/confidence, mark parse_method='llm',
    and set needs_review from the confidence gate. Identity fields (source,
    source_ref) always come from the deterministic pass.
    """
    if not llm:
        return deterministic
    artist = (llm.get("artist") or "").strip()
    title = (llm.get("title") or "").strip()
    if not artist and not title:
        return deterministic
    conf = llm.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.0
    out = dict(deterministic)
    out["artist"] = artist
    out["title"] = title
    out["confidence"] = conf
    out["parse_method"] = "llm"
    out["needs_review"] = 0 if conf >= threshold else 1
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_naming_merge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/naming.py kj-controller/tests/unit/test_naming_merge.py
git commit -m "feat(naming): merge_llm_result gate + youtube_id_from_url"
```

---

### Task B2: `GenClient.parse_titles`

**Files:**
- Modify: `kj-controller/gen_client.py`
- Test: `kj-controller/tests/unit/test_gen_client_parse.py`

**Interfaces:**
- Produces (used by B4, B8): `GenClient.parse_titles(items: list[dict]) -> list[dict] | None`. POSTs `{"items": items}` to `/api/parse-karaoke-titles`; returns `data["results"]` on success; returns `None` on **any** error (offline, timeout, non-200, missing endpoint) so callers fall back.

- [ ] **Step 1: Write failing tests**

```python
# kj-controller/tests/unit/test_gen_client_parse.py
from unittest.mock import patch, MagicMock
from gen_client import GenClient


def test_parse_titles_happy_path():
    c = GenClient("https://api.example.com", "tok")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"results": [{"id": "1", "artist": "Queen",
                                           "title": "Bohemian Rhapsody", "confidence": 0.9}]}
    resp.raise_for_status.return_value = None
    with patch("gen_client.requests.post", return_value=resp) as post:
        out = c.parse_titles([{"id": "1", "filename": "x.mp4"}])
    assert out[0]["artist"] == "Queen"
    args, kwargs = post.call_args
    assert args[0].endswith("/api/parse-karaoke-titles")
    assert kwargs["headers"]["X-Admin-Token"] == "tok"


def test_parse_titles_returns_none_on_error():
    c = GenClient("https://api.example.com", "tok")
    with patch("gen_client.requests.post", side_effect=Exception("offline")):
        assert c.parse_titles([{"id": "1", "filename": "x"}]) is None


def test_parse_titles_empty_items_returns_empty():
    c = GenClient("https://api.example.com", "tok")
    assert c.parse_titles([]) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_gen_client_parse.py -v`
Expected: FAIL (`AttributeError: parse_titles`).

- [ ] **Step 3: Implement in `gen_client.py`** (add method; larger timeout for the batch)

```python
    PARSE_TIMEOUT = 60

    def parse_titles(self, items):
        """Parse a batch of karaoke filenames -> [{id, artist, title, confidence}].

        Returns the results list on success, or None on ANY failure (offline,
        timeout, missing/undeployed endpoint, bad status) so the caller keeps
        its deterministic guess. Empty input short-circuits to [].
        """
        if not items:
            return []
        try:
            resp = requests.post(
                f"{self.api_url}/api/parse-karaoke-titles",
                json={"items": items},
                headers=self._headers(),
                timeout=self.PARSE_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results")
            return results if isinstance(results, list) else None
        except Exception as e:
            logger.warning("parse_titles failed (offline?): %s", e)
            return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_gen_client_parse.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/gen_client.py kj-controller/tests/unit/test_gen_client_parse.py
git commit -m "feat(gen_client): parse_titles batch call (offline-tolerant)"
```

---

### Task B3: `MediaLibraryStore.apply_parse` + config threshold

**Files:**
- Modify: `kj-controller/media_library.py`, `kj-controller/config.py`
- Test: `kj-controller/tests/unit/test_media_library_apply_parse.py`

**Interfaces:**
- Consumes: `text_normalize.normalize`.
- Produces (used by B4, B8): `MediaLibraryStore.apply_parse(media_id, artist, title, confidence, threshold) -> bool` — updates artist/title/`*_norm`, `confidence`, `parse_method='llm'`, `needs_review = 0 if confidence >= threshold else 1`, `updated_at`; returns whether a row was updated. Config: `parse_confidence_threshold` (float 0.75).

- [ ] **Step 1: Write failing tests**

```python
# kj-controller/tests/unit/test_media_library_apply_parse.py
from media_library import MediaLibraryStore


def _store():
    s = MediaLibraryStore(":memory:")
    s.upsert({"media_id": "yt-x", "source": "youtube", "artist": "A", "title": "B",
              "needs_review": 1, "confidence": 0.4})
    return s


def test_apply_parse_high_confidence_clears_review():
    s = _store()
    assert s.apply_parse("yt-x", "Queen", "Bohemian Rhapsody", 0.9, 0.75) is True
    row = s.get("yt-x")
    assert (row["artist"], row["title"]) == ("Queen", "Bohemian Rhapsody")
    assert row["needs_review"] == 0
    assert row["parse_method"] == "llm"
    assert row["confidence"] == 0.9
    assert row["artist_norm"]  # recomputed


def test_apply_parse_low_confidence_keeps_review():
    s = _store()
    s.apply_parse("yt-x", "Sublime", "Santeria", 0.5, 0.75)
    assert s.get("yt-x")["needs_review"] == 1


def test_apply_parse_missing_row_returns_false():
    s = _store()
    assert s.apply_parse("nope", "A", "B", 0.9, 0.75) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_media_library_apply_parse.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `apply_parse` in `media_library.py`** (after `set_metadata`)

```python
    def apply_parse(self, media_id, artist, title, confidence, threshold):
        """Apply an LLM parse result (parse_method='llm'); gate needs_review on
        the confidence threshold. Returns True if a row was updated."""
        needs_review = 0 if (confidence is not None and confidence >= threshold) else 1
        conn = self._get_conn()
        with self._lock():
            cur = conn.execute(
                """
                UPDATE media_library
                SET artist=?, title=?, artist_norm=?, title_norm=?,
                    confidence=?, parse_method='llm', needs_review=?,
                    updated_at=datetime('now')
                WHERE media_id=?
                """,
                (artist, title, _normalize(artist), _normalize(title),
                 confidence, needs_review, media_id),
            )
            conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 4: Add config key in `config.py`** (in `load_config` defaults, near `media_db_path`)

```python
        # LLM parse confidence gate: >= threshold clears needs_review.
        "parse_confidence_threshold": 0.75,
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_media_library_apply_parse.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/media_library.py kj-controller/config.py kj-controller/tests/unit/test_media_library_apply_parse.py
git commit -m "feat(media_library): apply_parse (LLM result) + confidence threshold config"
```

---

### Task B4: `MediaIndex` identity-finalize helper + `gen_client` slot

**Files:**
- Modify: `kj-controller/media.py`
- Test: `kj-controller/tests/unit/test_download_renaming.py` (helper-level tests)

**Interfaces:**
- Consumes: `naming.parse_identity`, `naming.merge_llm_result`, `naming.build_slug_filename`, `naming.media_id_for`, `MediaLibraryStore.upsert`, `GenClient.parse_titles`.
- Produces (used by B5):
  - `MediaIndex.__init__` sets `self.gen_client = None`.
  - `MediaIndex._finalize_download_identity(self, tmp_path, *, source, source_ref, artist_hint, title_hint, channel, raw_name, ext) -> (final_path, display_name, media_id)`:
    1. Build deterministic identity from hints (`{source, source_ref, artist, title}`), confidence 0.5.
    2. If `self.gen_client`: best-effort `parse_titles([{id, filename=raw_name, channel, source}])` → `merge_llm_result` (threshold from config). Any failure → keep deterministic.
    3. `media_id = media_id_for(source, source_ref)`.
    4. Destination dir = `<download_folder>/<source>/`; slug = `build_slug_filename(artist, title, media_id, ext)`; `os.replace(tmp_path, dest)` (collision-safe: same media_id ⇒ same slug ⇒ overwrite is correct).
    5. `upsert` the `media_library` row (`file_path=dest`, `raw_original_name=raw_name`).
    6. Return `(dest, "Artist - Title", media_id)`.

- [ ] **Step 1: Write failing tests**

```python
# kj-controller/tests/unit/test_download_renaming.py
import os
from media import MediaIndex
from media_library import MediaLibraryStore


def _mi(tmp_path, gen_client=None):
    cfg = {"download_folder": str(tmp_path), "media_folders": [str(tmp_path)],
           "media_index_path": str(tmp_path / "i.json"),
           "parse_confidence_threshold": 0.75}
    mi = MediaIndex(cfg, media_library=MediaLibraryStore(":memory:"))
    mi.gen_client = gen_client
    return mi


def test_finalize_writes_slug_into_source_folder_offline(tmp_path):
    mi = _mi(tmp_path)  # no gen_client -> deterministic
    src = tmp_path / "raw.mp4"
    src.write_bytes(b"\x00" * 16)
    final, display, media_id = mi._finalize_download_identity(
        str(src), source="youtube", source_ref="UM1XiyBmhM",
        artist_hint="Bella Kay", title_hint="iloveit", channel="Sing King",
        raw_name="-UM1XiyBmhM__Sing King__Bella Kay - iloveit.mp4", ext=".mp4")
    assert media_id == "yt-UM1XiyBmhM"
    assert os.path.dirname(final).endswith(os.path.join("", "youtube"))
    assert final.endswith("[yt-UM1XiyBmhM].mp4")
    assert os.path.exists(final) and not os.path.exists(src)
    row = mi.media_library.get("yt-UM1XiyBmhM")
    assert row["file_path"] == os.path.realpath(final)
    assert row["needs_review"] == 1  # offline deterministic


def test_finalize_uses_llm_when_gen_client_present(tmp_path):
    class FakeGen:
        def parse_titles(self, items):
            return [{"id": items[0]["id"], "artist": "Sublime",
                     "title": "Santeria", "confidence": 0.95}]
    mi = _mi(tmp_path, gen_client=FakeGen())
    src = tmp_path / "raw.mp4"; src.write_bytes(b"\x00" * 16)
    final, display, media_id = mi._finalize_download_identity(
        str(src), source="youtube", source_ref="ABCDEFGHIJK",
        artist_hint="Santeria", title_hint="Sublime", channel="KaraFun",
        raw_name="Santeria - Sublime _ KaraFun.mp4", ext=".mp4")
    row = mi.media_library.get("yt-ABCDEFGHIJK")
    assert (row["artist"], row["title"]) == ("Sublime", "Santeria")
    assert row["needs_review"] == 0 and row["parse_method"] == "llm"
    assert display == "Sublime - Santeria"


def test_finalize_survives_llm_exception(tmp_path):
    class BoomGen:
        def parse_titles(self, items):
            raise RuntimeError("offline")
    mi = _mi(tmp_path, gen_client=BoomGen())
    src = tmp_path / "raw.mp4"; src.write_bytes(b"\x00" * 16)
    final, display, media_id = mi._finalize_download_identity(
        str(src), source="community", source_ref="WTF-abc123",
        artist_hint="Queen", title_hint="Bohemian Rhapsody", channel=None,
        raw_name="WTF - Queen - Bohemian Rhapsody.mp4", ext=".mp4")
    assert media_id == "db-WTF-abc123"
    assert os.path.exists(final)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_download_renaming.py -v`
Expected: FAIL (`AttributeError: _finalize_download_identity`).

- [ ] **Step 3: Implement in `media.py`**

Add to imports (top, extend the existing `from naming import ...`):

```python
from naming import (
    parse_identity, extract_media_id, media_id_for, content_hash,
    build_slug_filename, merge_llm_result, SOURCE_UPLOAD,
)
```

In `__init__`, add:

```python
        self.gen_client = None  # attribute-injected by app.py after gen_client built
```

Add the method (near the other download helpers):

```python
    def _finalize_download_identity(self, tmp_path, *, source, source_ref,
                                    artist_hint, title_hint, channel,
                                    raw_name, ext):
        """Resolve canonical identity for a freshly-downloaded file, move it to
        <download_folder>/<source>/<slug>, and upsert media_library.

        Deterministic hints first; then a best-effort LLM refine (offline / any
        failure -> keep deterministic + needs_review=1). Returns
        (final_path, display_name, media_id).
        """
        threshold = float(self.config.get("parse_confidence_threshold", 0.75) or 0.75)
        identity = {
            "source": source, "source_ref": source_ref,
            "artist": (artist_hint or "").strip(), "title": (title_hint or "").strip(),
            "confidence": 0.5, "needs_review": 1, "parse_method": "deterministic",
        }
        if self.gen_client is not None:
            try:
                res = self.gen_client.parse_titles([{
                    "id": "1", "filename": raw_name,
                    "channel": channel or "", "source": source,
                }])
                llm = res[0] if res else None
                identity = merge_llm_result(identity, llm, threshold)
            except Exception as exc:
                log_message(f"LLM refine failed for {raw_name}: {exc}", self.config)

        media_id = media_id_for(source, source_ref)
        dest_dir = os.path.join(
            self.config.get("download_folder", ""), source)
        os.makedirs(dest_dir, exist_ok=True)
        slug = build_slug_filename(identity["artist"], identity["title"], media_id, ext)
        dest = os.path.join(dest_dir, slug)
        os.replace(tmp_path, dest)
        real_dest = os.path.realpath(dest)

        display = " - ".join(p for p in (identity["artist"], identity["title"]) if p) or slug
        if self.media_library is not None:
            try:
                self.media_library.upsert({
                    "media_id": media_id, "source": source, "source_ref": source_ref,
                    "artist": identity["artist"], "title": identity["title"],
                    "confidence": identity["confidence"],
                    "parse_method": identity["parse_method"],
                    "needs_review": identity["needs_review"],
                    "raw_original_name": raw_name, "file_path": real_dest, "ext": ext,
                })
            except Exception as exc:
                log_message(f"media_library upsert failed for {slug}: {exc}", self.config)
        return real_dest, display, media_id
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_download_renaming.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/media.py kj-controller/tests/unit/test_download_renaming.py
git commit -m "feat(media): _finalize_download_identity (slug + source folder + LLM refine)"
```

---

### Task B5: Route the three download methods through the finalizer

**Files:**
- Modify: `kj-controller/media.py` (`download_video`, `download_from_url`, `download_cdg_pair`)
- Test: `kj-controller/tests/unit/test_download_renaming.py` (add method-level tests); re-run `tests/unit/test_media.py`

**Interfaces:**
- Consumes: `_finalize_download_identity` (B4).
- Produces (used by B6 worker): new signatures
  - `download_video(self, youtube_url)` — unchanged signature; now writes to `youtube/` with `yt-<id>` slug, gate runs on the temp file *before* finalize.
  - `download_from_url(self, url, filename=None, *, source="community", source_ref=None, artist=None, title=None, channel=None)` — `source` selects the subfolder + `media_id` scheme; `source_ref` required for a stable id (else falls back to `content_hash`).
  - `download_cdg_pair(self, cdg_url, mp3_url, filename, *, source="community", source_ref=None, artist=None, title=None)`.

**Design notes for the implementer:** keep the existing playability gate — run it on the downloaded temp file *before* calling `_finalize_download_identity` (finalize only moves/renames a file that already passed). Preserve the `index[real_path]` entry + `self.save()` (now keyed on the final path) so the legacy `media_index.json` stays correct, and add `entry["media_id"]`. For `source_ref`: youtube → `youtube_id`; community → `f"{brand or 'DB'}-{source_ref_fileid}"` when the caller passes the Drive file_id, else `content_hash`; gen → `job8` (caller passes it). When no `source_ref` is derivable, `source_ref = content_hash(tmp_path)` and `source=upload`.

- [ ] **Step 1: Add failing method-level tests**

```python
# kj-controller/tests/unit/test_download_renaming.py  (append)
from unittest.mock import patch


def test_download_from_url_gen_source_lands_in_gen_folder(tmp_path):
    mi = _mi(tmp_path)
    def fake_http(url, path):
        if path:
            with open(path, "wb") as f: f.write(b"\x00" * 16)
        return None
    with patch.object(mi, "_http_download", side_effect=fake_http), \
         patch("media._gate_playable") as gate:
        gate.return_value.verdict = {"overall_ok": True, "reasons": []}
        real, display = mi.download_from_url(
            "https://x/y.mp4", filename="GEN-1a2b3c4d - Cher - Believe.mp4",
            source="gen", source_ref="1a2b3c4d", artist="Cher", title="Believe")
    assert os.sep + "gen" + os.sep in real
    assert real.endswith("[gen-1a2b3c4d].mp4")
    assert mi.media_library.get("gen-1a2b3c4d")["source"] == "gen"


def test_download_from_url_community_lands_in_community_folder(tmp_path):
    mi = _mi(tmp_path)
    def fake_http(url, path):
        if path:
            with open(path, "wb") as f: f.write(b"\x00" * 16)
        return None
    with patch.object(mi, "_http_download", side_effect=fake_http), \
         patch("media._gate_playable") as gate:
        gate.return_value.verdict = {"overall_ok": True, "reasons": []}
        real, display = mi.download_from_url(
            "https://x/y.mp4", filename="WTF - Queen - Bohemian.mp4",
            source="community", source_ref="WTF-drivefileid123",
            artist="Queen", title="Bohemian Rhapsody")
    assert os.sep + "community" + os.sep in real
    assert mi.media_library.get("db-WTF-drivefileid123") is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_download_renaming.py -v`
Expected: FAIL (`download_from_url` has no `source` kwarg).

- [ ] **Step 3: Refactor the three methods**

For each method: download to a temp path inside the *download root* (or a `tempfile` staging dir), run the existing playability gate on it, and on pass call `_finalize_download_identity(...)` with the right `source`/`source_ref`/hints; build the legacy index `entry` on the returned `real_dest` (add `entry["media_id"]`), `self.save()`, and return `(real_dest, display)`. On gate failure, keep the existing quarantine behavior. **`download_video`**: after locating the merged file, use it as the temp; `source="youtube"`, `source_ref=youtube_id`, `artist_hint`/`title_hint` from `parse_identity(basename)` (or `parse_youtube_filename`), `channel=safe_channel`, `raw_name=os.path.basename(temp)`. **`download_from_url`**: stage the body to a temp path (reuse current write), then finalize with the passed `source`/`source_ref`/`artist`/`title` (default `source="community"`; when `source_ref` is falsy, set `source="upload"`, `source_ref=content_hash(tmp)`). **`download_cdg_pair`**: build the zip in the temp dir (as today), gate it, then finalize with `ext=".zip"`, `source="community"`, passed `source_ref`.

(Reproduce the full method bodies during implementation — do not leave prose. Keep behavior identical except for the destination/naming/upsert.)

- [ ] **Step 4: Run to verify all pass (incl. existing media tests)**

Run: `cd kj-controller && pytest tests/unit/test_download_renaming.py tests/unit/test_media.py -v`
Expected: PASS. Fix any existing `test_media.py` expectation that asserted the old `divebar__`/flat-folder names (update to the new source-folder slug — the old behavior is intentionally replaced).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/media.py kj-controller/tests/unit/test_download_renaming.py kj-controller/tests/unit/test_media.py
git commit -m "feat(media): downloads land in source folders with slug + media_id"
```

---

### Task B6: Wire `gen_client` into `MediaIndex` + thread identity through the worker

**Files:**
- Modify: `kj-controller/app.py` (both factory paths), `kj-controller/routes.py` (`_resolve_divebar_spec`, `_download_worker`)
- Test: `kj-controller/tests/unit/test_app_media_gen_client.py` (new), extend `tests/unit/test_download_worker*.py` if present

**Interfaces:**
- Consumes: `_finalize_download_identity` via the download methods.
- Produces: `flask_app.media.gen_client is flask_app.gen_client`; `_resolve_divebar_spec` spec carries `artist`, `title`, `brand_code`, `divebar_file_id`; `_download_worker` passes `source`/`source_ref`/`artist`/`title` to `download_from_url`/`download_cdg_pair`.

- [ ] **Step 1: Write failing test (wiring)**

```python
# kj-controller/tests/unit/test_app_media_gen_client.py
import app as app_module


def test_media_gets_gen_client_when_configured(tmp_path, monkeypatch):
    cfg = {
        "media_folders": [], "download_folder": str(tmp_path),
        "media_index_path": str(tmp_path / "i.json"),
        "media_db_path": str(tmp_path / "m.db"),
        "gen_api_url": "https://api.example.com", "gen_api_token": "tok",
        "flask_port": 80, "websockify_enabled": False,
    }
    monkeypatch.setattr(app_module, "load_config", lambda *a, **k: cfg)
    flask_app = app_module.create_app()
    assert flask_app.media.gen_client is flask_app.gen_client
    assert flask_app.gen_client is not None
```

(If `create_app()` is heavy, assert against the smallest wiring slice, mirroring the Phase-1 `test_app_media_library_wired.py` approach.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_app_media_gen_client.py -v`
Expected: FAIL (`media.gen_client is None`).

- [ ] **Step 3: Inject in `app.py`** — after each `flask_app.gen_client = GenClient(...)` line (both factory paths, ~L299 and ~L476), add:

```python
        if getattr(flask_app, "media", None) is not None:
            flask_app.media.gen_client = flask_app.gen_client
```

(At the second/worker factory path, the local is `media` not `flask_app.media`; set `media.gen_client = flask_app.gen_client` there.)

- [ ] **Step 4: Thread identity through `_resolve_divebar_spec` + `_download_worker`** in `routes.py`

In `_resolve_divebar_spec`, add `"artist": artist, "title": title, "brand_code": brand_code` to BOTH returned spec dicts (pair + single). In `_download_worker`, update the divebar branch:

```python
            if next_item.get('source') == 'divebar':
                fid = next_item.get('divebar_file_id')
                brand = next_item.get('brand_code') or ''
                ref = f"{brand or 'DB'}-{fid}" if fid else None
                if next_item.get('pair'):
                    file_path, title = app.media.download_cdg_pair(
                        next_item['cdg_url'], next_item['mp3_url'],
                        filename=next_item.get('title'),
                        source="community", source_ref=ref,
                        artist=next_item.get('artist'), title=next_item.get('title'))
                else:
                    file_path, title = app.media.download_from_url(
                        next_item['url'], filename=next_item.get('title'),
                        source="community", source_ref=ref,
                        artist=next_item.get('artist'), title=next_item.get('title'))
            else:
                file_path, title = app.media.download_video(next_item['url'])
```

**Note:** the `title` key in the spec is the *zip/filename* (e.g. `WTF - Queen - ...`), not the song title. Add a distinct `song_title`/`song_artist` to the spec so the worker passes real values — update `_resolve_divebar_spec` to also emit `"song_artist": artist, "song_title": title` and have the worker read those for `artist=`/`title=`. (Pick unambiguous keys; don't overload `title`.)

- [ ] **Step 5: Update `gen_poller._handle_complete`** to pass gen identity (fixes the `divebar__` mislabel):

```python
            file_path, _ = self.media.download_from_url(
                download_url, filename=filename,
                source="gen", source_ref=job_id[:8], artist=artist, title=title)
```

(`artist`/`title` are already parsed in that method; note its current split is `"Title - Artist"` — keep passing them in the correct artist/title args.)

- [ ] **Step 6: Run to verify**

Run: `cd kj-controller && pytest tests/unit/test_app_media_gen_client.py tests/unit/test_download_renaming.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kj-controller/app.py kj-controller/routes.py kj-controller/gen_poller.py kj-controller/tests/unit/test_app_media_gen_client.py
git commit -m "feat: inject gen_client into MediaIndex + thread identity to downloads"
```

---

### Task B7: Dedup-skip helpers

**Files:**
- Modify: `kj-controller/routes.py`
- Test: `kj-controller/tests/unit/test_dedup_skip.py`

**Interfaces:**
- Consumes: `naming.youtube_id_from_url`, `naming.media_id_for`, `MediaLibraryStore.get`.
- Produces (used by B8):
  - `_prospective_media_id(source, *, youtube_url=None, file_id=None, brand_code=None) -> str | None`.
  - `_existing_media_for(app, media_id) -> dict | None` — returns the media_library row **only if** `media_id` is set, the row exists, and its `file_path` exists on disk; else `None`.

- [ ] **Step 1: Write failing tests**

```python
# kj-controller/tests/unit/test_dedup_skip.py
import os
import types
import routes
from media_library import MediaLibraryStore


def test_prospective_media_id_youtube():
    assert routes._prospective_media_id(
        "youtube", youtube_url="https://youtu.be/UM1XiyBmhM") == "yt-UM1XiyBmhM"


def test_prospective_media_id_divebar():
    assert routes._prospective_media_id(
        "divebar", file_id="drivefileid123", brand_code="WTF") == "db-WTF-drivefileid123"


def test_prospective_media_id_unknown_returns_none():
    assert routes._prospective_media_id("youtube", youtube_url="garbage") is None


def test_existing_media_requires_file_on_disk(tmp_path):
    store = MediaLibraryStore(":memory:")
    f = tmp_path / "x.mp4"; f.write_bytes(b"0")
    store.upsert({"media_id": "yt-a", "source": "youtube", "file_path": str(f)})
    store.upsert({"media_id": "yt-gone", "source": "youtube",
                  "file_path": str(tmp_path / "missing.mp4")})
    app = types.SimpleNamespace(media_library=store)
    assert routes._existing_media_for(app, "yt-a")["media_id"] == "yt-a"
    assert routes._existing_media_for(app, "yt-gone") is None
    assert routes._existing_media_for(app, None) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_dedup_skip.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement in `routes.py`** (add `from naming import youtube_id_from_url, media_id_for` at top)

```python
def _prospective_media_id(source, *, youtube_url=None, file_id=None, brand_code=None):
    """Cheap pre-download media_id for dedup-skip, or None if not derivable."""
    if source == "youtube":
        vid = youtube_id_from_url(youtube_url)
        return media_id_for("youtube", vid) if vid else None
    if source == "divebar" and file_id:
        return media_id_for("community", f"{(brand_code or 'DB')}-{file_id}")
    return None


def _existing_media_for(app, media_id):
    """Return the media_library row for media_id iff its file exists on disk."""
    if not media_id:
        return None
    store = getattr(app, "media_library", None)
    if store is None:
        return None
    row = store.get(media_id)
    if row and row.get("file_path") and os.path.exists(row["file_path"]):
        return row
    return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_dedup_skip.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_dedup_skip.py
git commit -m "feat(routes): dedup-skip helpers (_prospective_media_id, _existing_media_for)"
```

---

### Task B8: Wire dedup-skip into the four enqueue sites

**Files:**
- Modify: `kj-controller/routes.py` (`handle_download`, `divebar_download`, `download_and_link_rotation`, `approve_sing_request`)
- Test: `kj-controller/tests/unit/test_dedup_skip.py` (add integration cases with the Flask test client)

**Interfaces:**
- Consumes: `_prospective_media_id`, `_existing_media_for` (B7); `rotation.link_file` / `rotation.complete_download`.
- Behavior:
  - **`handle_download`** (plain YouTube): before enqueue, if `_existing_media_for(app, _prospective_media_id("youtube", youtube_url=url))` → return `{"success": True, "deduped": True, "file_path": ...}` (no queue item).
  - **`divebar_download`**: compute `db-<brand>-<file_id>`; if present → return `{"success": True, "deduped": True, "file_path": ...}`.
  - **`download_and_link_rotation`** + **`approve_sing_request`**: if the prospective file exists → **link it to the rotation entry directly** (`rotation.set_download_status(entry_id, source, "complete", ...)` + `rotation.link_file(entry_id, file_path)` / `rotation.complete_download`), skip the queue, return success with the decorated entries.

- [ ] **Step 1: Write failing integration test** (YouTube plain-download dedup via test client)

```python
# kj-controller/tests/unit/test_dedup_skip.py  (append)
def test_handle_download_dedupes_existing_youtube(tmp_path, monkeypatch):
    # Build a minimal app with a media_library containing the video, file present.
    import app as app_module
    f = tmp_path / "v.mp4"; f.write_bytes(b"0")
    # ... construct app via create_app() with a temp config, upsert yt-<id>
    #     pointing file_path at f, POST /download {url: youtu.be/<id>},
    #     assert 200 + body["deduped"] is True and no queue item was added.
```

(Flesh this out against `create_app()` like the existing route tests; if full app construction is heavy in unit tests, test the two enqueue branches by calling the view function with a pushed app context and a stubbed `app.media_library`/`app.rotation`. Keep at least one end-to-end assertion that a dup URL does NOT append to `app.download_queue['items']`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_dedup_skip.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the four wirings** (surgical inserts before each enqueue)

For `handle_download`, after resolving `url` and before building the queue item:

```python
    existing = _existing_media_for(app, _prospective_media_id("youtube", youtube_url=url))
    if existing:
        log_message(f"Dedup-skip: already have {existing['media_id']}", cfg)
        return jsonify({"success": True, "deduped": True,
                        "file_path": existing["file_path"]})
```

For `divebar_download`, after `file_id`/`brand_code` are known and before `_resolve_divebar_spec`:

```python
    existing = _existing_media_for(app, _prospective_media_id(
        "divebar", file_id=file_id, brand_code=brand_code))
    if existing:
        return jsonify({"success": True, "deduped": True,
                        "file_path": existing["file_path"]})
```

For `download_and_link_rotation` (both divebar + youtube) and `approve_sing_request`: compute the prospective id from the source, and if `_existing_media_for` returns a row, get/create the rotation entry, then link + mark complete instead of enqueuing:

```python
    existing = _existing_media_for(app, prospective_id)
    if existing:
        rotation.set_download_status(entry_id, source, "complete", None)
        rotation.link_file(entry_id, existing["file_path"])
        entries = rotation.get_rotation(); _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "deduped": True, "entries": entries})
```

(For `approve_sing_request`, which returns an `entry_id` not a Response, link + return the `entry_id` without queuing.)

- [ ] **Step 4: Run to verify pass**

Run: `cd kj-controller && pytest tests/unit/test_dedup_skip.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_dedup_skip.py
git commit -m "feat(routes): dedup-skip at the four download enqueue sites"
```

---

### Task B9: `scripts/refine_titles.py` — backlog refine

**Files:**
- Create: `kj-controller/scripts/refine_titles.py`
- Test: `kj-controller/tests/unit/test_refine_titles.py`

**Interfaces:**
- Consumes: `MediaLibraryStore.list_records(needs_review=1)`, `MediaLibraryStore.apply_parse`, `GenClient.parse_titles`, `naming.merge_llm_result`.
- Produces: `run_refine(store, gen_client, *, threshold=0.75, batch_size=100, dry_run=True) -> dict` returning `{"total": int, "refined": int, "unchanged": int, "offline": bool}`; a `main()` CLI (`--execute`, `--threshold`, `--batch-size`) that loads config, builds the store + `GenClient`.

- [ ] **Step 1: Write failing tests**

```python
# kj-controller/tests/unit/test_refine_titles.py
from media_library import MediaLibraryStore
import scripts.refine_titles as rt


class FakeGen:
    def __init__(self, mapping): self.mapping = mapping
    def parse_titles(self, items):
        return [{"id": it["id"], **self.mapping.get(it["id"], {"artist": "", "title": "", "confidence": 0.0})}
                for it in items]


def _store():
    s = MediaLibraryStore(":memory:")
    s.upsert({"media_id": "yt-a", "source": "youtube",
              "artist": "Santeria", "title": "Sublime", "needs_review": 1,
              "confidence": 0.4, "raw_original_name": "Santeria - Sublime KaraFun.mp4"})
    return s


def test_dry_run_does_not_write():
    s = _store()
    gen = FakeGen({"yt-a": {"artist": "Sublime", "title": "Santeria", "confidence": 0.95}})
    out = rt.run_refine(s, gen, dry_run=True)
    assert out["refined"] == 1
    assert s.get("yt-a")["needs_review"] == 1  # unchanged on disk


def test_execute_applies_high_confidence():
    s = _store()
    gen = FakeGen({"yt-a": {"artist": "Sublime", "title": "Santeria", "confidence": 0.95}})
    out = rt.run_refine(s, gen, dry_run=False)
    row = s.get("yt-a")
    assert (row["artist"], row["title"]) == ("Sublime", "Santeria")
    assert row["needs_review"] == 0 and row["parse_method"] == "llm"


def test_offline_gen_none_is_noop():
    s = _store()
    class Offline:
        def parse_titles(self, items): return None
    out = rt.run_refine(s, Offline(), dry_run=False)
    assert out["offline"] is True and out["refined"] == 0
    assert s.get("yt-a")["needs_review"] == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_refine_titles.py -v`
Expected: FAIL (`ModuleNotFoundError: scripts.refine_titles`).

- [ ] **Step 3: Implement `scripts/refine_titles.py`**

```python
# kj-controller/scripts/refine_titles.py
"""Batch-refine needs_review media_library rows via the gen parse endpoint.

DB-ONLY: updates artist/title/needs_review (never renames files — that is the
Phase-4 migration). Dry-run by default. Offline (gen unavailable) -> no-op.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_config  # noqa: E402
from media_library import MediaLibraryStore  # noqa: E402


def run_refine(store, gen_client, *, threshold=0.75, batch_size=100, dry_run=True):
    rows = store.list_records(needs_review=1)
    total = len(rows)
    refined = unchanged = 0
    offline = False
    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        items = [{"id": r["media_id"],
                  "filename": r.get("raw_original_name") or r.get("file_path") or "",
                  "source": r.get("source")} for r in batch]
        results = gen_client.parse_titles(items) if gen_client else None
        if results is None:
            offline = True
            break
        by_id = {str(r.get("id")): r for r in results}
        for r in batch:
            llm = by_id.get(r["media_id"])
            artist = (llm or {}).get("artist", "").strip() if llm else ""
            title = (llm or {}).get("title", "").strip() if llm else ""
            conf = (llm or {}).get("confidence") if llm else None
            if not artist and not title:
                unchanged += 1
                continue
            refined += 1
            if not dry_run:
                store.apply_parse(r["media_id"], artist, title, conf, threshold)
    return {"total": total, "refined": refined, "unchanged": unchanged, "offline": offline}


def main():
    ap = argparse.ArgumentParser(description="Refine needs_review media rows via gen LLM")
    ap.add_argument("--execute", action="store_true", help="apply changes (default dry-run)")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--batch-size", type=int, default=100)
    args = ap.parse_args()

    cfg = load_config()
    threshold = args.threshold if args.threshold is not None else float(
        cfg.get("parse_confidence_threshold", 0.75) or 0.75)
    store = MediaLibraryStore(cfg.get("media_db_path"))
    gen_client = None
    url, tok = cfg.get("gen_api_url", ""), cfg.get("gen_api_token", "")
    if url and tok:
        from gen_client import GenClient
        gen_client = GenClient(url, tok)
    out = run_refine(store, gen_client, threshold=threshold,
                     batch_size=args.batch_size, dry_run=not args.execute)
    print(("DRY-RUN " if not args.execute else "") + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_refine_titles.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/scripts/refine_titles.py kj-controller/tests/unit/test_refine_titles.py
git commit -m "feat(scripts): refine_titles backlog batch-refine (dry-run default, DB-only)"
```

---

### Task B10: Full regression + version bump + docs

**Files:**
- Modify: `kj-controller/pyproject.toml` (version bump: `0.51.1` → `0.52.0`)
- Modify: `docs/CHANGELOG.md` (dated entry); optionally note the new endpoint dependency in `docs/ARCHITECTURE.md`.

- [ ] **Step 1: Full suite**

Run: `cd kj-controller && pytest -q`
Expected: PASS (all new modules + no regressions). Investigate any failure; do not dismiss as pre-existing.

- [ ] **Step 2: Bump version + changelog**

Bump `version` in `kj-controller/pyproject.toml` to `0.52.0`. Add a dated `docs/CHANGELOG.md` entry (LLM download-naming P2: gen endpoint, download renaming, dedup-skip, refine script).

- [ ] **Step 3: Commit**

```bash
git add kj-controller/pyproject.toml docs/CHANGELOG.md docs/ARCHITECTURE.md
git commit -m "chore: bump to 0.52.0 for download-naming P2 (LLM parse + renaming + dedup)"
```

---

## Self-Review (against the spec)

- **§4 gen endpoint** — PR-A A1-A4: `POST /api/parse-karaoke-titles`, batch contract exactly as specced, `X-Admin-Token`/`require_admin`, reuses Vertex client, kill-switch + graceful degrade. ✓
- **§3 pipeline (deterministic → LLM → gate)** — B4 `_finalize_download_identity` (deterministic hints → `parse_titles` → `merge_llm_result` gate). ✓
- **§5 download-flow renaming** — B5/B6: three methods write `Artist - Title [media_id].ext` into `downloads/<source>/`, upsert `media_library`; gen path `source=gen` (fixes `divebar__` mislabel). ✓
- **§5 dedup-skip** — B7/B8: prospective `media_id` at the 4 enqueue sites; rotation sites link existing file. ✓
- **Refine pass (locked decision 1)** — B9 `scripts/refine_titles.py`, dry-run default, DB-only, offline-tolerant. ✓
- **Offline resilience** — `parse_titles` returns `None`/blanks on any failure; `_finalize_download_identity` try/except; refine no-ops offline. ✓
- **media_id round-trip** — slug embeds `[media_id]`; `scan()`'s `extract_media_id` recovers it (no dual-id for new downloads). YouTube dedup catches the backlog (scan derives `yt-<id>`); community dedup is forward-looking (documented). ✓
- **Not in scope (later phases):** Available Songs edit UX + rotation `Artist - Title` flip (P3); reviewed backlog file migration (P4). ✓
- **Type consistency:** `parse_titles` result shape `{id, artist, title, confidence}`; `merge_llm_result`/`apply_parse` signatures; spec keys `song_artist`/`song_title` threaded (not overloading `title`) — consistent across B1→B9.
- **Placeholder scan:** B5 and B8 intentionally defer *full method-body reproduction* to implementation with explicit design notes (large existing functions); every other step has concrete code. Implementers must reproduce full bodies, not prose.

## Rollout

- **PR-A first** (karaoke-gen): `/coderabbit` → `/pr` → `make test` green → merge → auto-deploys to Cloud Run. Verify: `curl -X POST https://api.nomadkaraoke.com/api/parse-karaoke-titles -H "X-Admin-Token: $TOKEN" -d '{"items":[{"id":"1","filename":"Santeria - Sublime _ KaraFun.mp4","source":"youtube"}]}'` → expect `{results:[{artist:"Sublime", title:"Santeria", ...}]}`.
- **PR-B** (kjbox): `/coderabbit` → `/pr` → merge. Deploy off-show (confirm no live event; back up DBs): `git pull` on `/opt/nomad/kjbox` → restart `kj-controller` → verify a fresh YouTube + divebar download lands in `downloads/youtube|community/` with a slug name and a `media_library` row; run `refine_titles.py` (dry-run) and confirm it reaches the endpoint; re-download an existing song and confirm dedup-skip. `ssh nomadpctunnel`; app on `app_bind_port` 5001; venv python.
