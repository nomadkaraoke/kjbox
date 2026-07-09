# Singer Search Reliability & Mis-tap Prevention — Implementation Plan (PR #1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop singers ending up with a song they didn't pick, by fixing the search response race, stopping the results list shifting under a finger, hardening the confirmation screen, and porting the KJ search's version-list presentation.

**Architecture:** Frontend-only changes to the singer SPA (`kj-controller/static-sing/sing.js` + `sing.css`). The singer search shares the KJ search's live-scraping backend (`unified_search`) but lacks every reliability protection the KJ search has. We port the KJ's `rotSearchGen` stale-response guard, add a render-stability guard + tap cooldown, restructure the confirm screen, and reuse the `priority_*` metadata already present in the grouped `versions[]` for Best/trusted markers.

**Tech Stack:** Vanilla JS (no framework, no build step, ES module), CSS, Playwright (`pytest`) for e2e. Tests live in `kj-controller/tests/e2e/test_sing_frontend.py`.

## Global Constraints

- **No build step / no framework.** Plain ES-module JS in `static-sing/sing.js`; DOM built via the local `el(tag, attrs, ...children)` helper.
- **Test bridge:** `window.__sing_state` and `window.__sing_render()` are exposed for Playwright (`sing.js:1563-1566`). Drive states by assigning `window.__sing_state.*` then calling `window.__sing_render()`.
- **Search backend is slow & variable** (`GET /sing/search` → `unified_search`, live-scrapes Karaoke Nerds, up to ~8s). Correctness must come from the generation guard, not the debounce delay.
- **Do NOT add `config=cfg`** to the singer's `render_template` in `sing.py` — the template relies on Jinja's auto-injected `config` for `APP_VERSION`; adding it reintroduces the cache-bust shadow bug fixed on the KJ side (`123844b`).
- **Frontend deploy needs a cache-bust:** bump the version in `pyproject.toml` in this PR (assets are loaded `?v={{ config.get('APP_VERSION') }}`; the service worker uses `__APP_VERSION__`). Frontend-only changes auto-deploy without a service restart.
- **Run tests:** `cd kj-controller && pytest tests/e2e/test_sing_frontend.py -v` (Playwright chromium must be installed; the repo's e2e suite already uses it).
- **Commit style:** end commit messages with the repo's `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

---

### Task 1: Stale-response generation guard + latest-owner rule + longer debounce

Ports `rotSearchGen` (`static/app.js:7121-7216`). Fixes the primary wrong-song race: a slow response for an earlier query overwriting newer results.

**Files:**
- Modify: `kj-controller/static-sing/sing.js` — `renderSearch()` `doSearch` (currently `sing.js:526-549`)
- Test: `kj-controller/tests/e2e/test_sing_frontend.py`

**Interfaces:**
- Consumes: existing `search(q)` (`sing.js:139-142`), closure vars `results`, `loading`, `err`, and `update()` inside `renderSearch`.
- Produces: no new exports. Internal `searchGen` counter governing which response may render.

- [ ] **Step 1: Write the failing test** — append to `test_sing_frontend.py`:

```python
class TestSearchRace:
    def _goto_search(self, page, live_server, live_token, name="Alice"):
        _login(page, live_server, live_token, name=name)
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")

    def test_stale_response_does_not_clobber_newer(self, page, live_server, live_token):
        """An earlier, slower query's response must not overwrite a newer one."""
        # Slow response for 'aaa' (queen), fast response for 'aaab' (abba).
        def handle(route):
            q = route.request.url.split("q=")[1].split("&")[0]
            if q == "aaa":
                page.wait_for_timeout(600)  # arrive AFTER the newer query
                body = {"songs": [{"key": "queen:1", "artist": "Queen",
                                   "title": "SLOW STALE", "version_count": 1,
                                   "versions": [{"source": "local",
                                                 "local": {"path": "/x", "artist": "Queen",
                                                           "title": "SLOW STALE"}}]}]}
            else:
                body = {"songs": [{"key": "abba:1", "artist": "ABBA",
                                   "title": "FAST FRESH", "version_count": 1,
                                   "versions": [{"source": "local",
                                                 "local": {"path": "/y", "artist": "ABBA",
                                                           "title": "FAST FRESH"}}]}]}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        page.route("**/sing/search*", handle)
        self._goto_search(page, live_server, live_token)
        inp = page.locator('input[type="search"]')
        inp.fill("aaa")     # schedules the slow query
        inp.fill("aaab")    # supersedes it with the fast query
        expect(page.locator(".r-title")).to_have_text("FAST FRESH")
        # Give the stale response time to (wrongly) land, then assert it never does.
        page.wait_for_timeout(900)
        expect(page.locator(".r-title")).to_have_text("FAST FRESH")
        expect(page.locator(".results")).not_to_contain_text("SLOW STALE")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestSearchRace::test_stale_response_does_not_clobber_newer -v`
Expected: FAIL — the stale "SLOW STALE" response overwrites the results (assertion on `.r-title` fails, or `.results` contains "SLOW STALE").

- [ ] **Step 3: Implement the generation guard** — replace `doSearch` (`sing.js:526-549`) with:

```js
  let debounceTimer = null;
  // Generation guard (ported from the KJ link search, app.js rotSearchGen):
  // bumped at the start of each fetch so a slower earlier query cannot clobber
  // a newer one, and so only the latest search may clear loading / set err.
  let searchGen = 0;
  const doSearch = (q) => {
    clearTimeout(debounceTimer);
    // 700ms (was 300) to match the KJ side — the shared backend live-scrapes,
    // so a longer debounce just trims wasted scrapes. Correctness comes from
    // the generation guard below, not from the delay.
    debounceTimer = setTimeout(async () => {
      const myGen = ++searchGen;
      if (q.trim().length < 3) {
        results = { songs: [] };
        if (myGen === searchGen) { loading = false; update(); }
        return;
      }
      loading = true; err = ""; update();
      try {
        const data = await search(q.trim());
        if (myGen !== searchGen) return;   // superseded — discard stale response
        results = data;
        if (typeof data.make_requests_enabled === "boolean") {
          state.makeRequestsEnabled = data.make_requests_enabled;
        }
        if (typeof data.simple_mode === "boolean") {
          state.simpleMode = data.simple_mode;
        }
      } catch (e) {
        if (myGen !== searchGen) return;   // stale failure — don't clobber a live search
        err = "Search failed. Try again.";
      } finally {
        if (myGen === searchGen) { loading = false; update(); }  // latest-owner rule
      }
    }, 700);
  };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestSearchRace::test_stale_response_does_not_clobber_newer -v`
Expected: PASS.

- [ ] **Step 5: Run the full singer e2e file to check for regressions**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py -v`
Expected: all PASS (existing search/confirm tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "fix(sing): guard singer search against stale out-of-order responses

Port the KJ rotSearchGen generation guard + latest-owner rule and raise the
debounce to 700ms. A slow earlier query can no longer clobber newer results.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Instant "Searching…" feedback on keystroke

The debounce is now 700ms; without immediate feedback the UI feels dead while typing. Mirror the KJ's immediate spinner (`app.js:7138-7142`).

**Files:**
- Modify: `kj-controller/static-sing/sing.js` — the search `<input>` `oninput` handler (`sing.js:977-983`) and `renderResults()` loading branch (`sing.js:909`).
- Test: `kj-controller/tests/e2e/test_sing_frontend.py`

**Interfaces:**
- Consumes: closure vars `loading`, `update()`; `state.query`.
- Produces: none.

- [ ] **Step 1: Write the failing test**

```python
    def test_typing_shows_searching_immediately(self, page, live_server, live_token):
        """The 'Searching…' hint must appear on keystroke, before the 700ms debounce."""
        def handle(route):
            page.wait_for_timeout(1500)  # hold the response open
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"songs": []}))
        page.route("**/sing/search*", handle)
        self._goto_search(page, live_server, live_token)
        page.locator('input[type="search"]').fill("bohemian")
        # Well under the 700ms debounce + the 1500ms held response:
        expect(page.locator(".results .hint")).to_have_text("Searching…", timeout=400)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestSearchRace::test_typing_shows_searching_immediately -v`
Expected: FAIL — "Searching…" only appears after the 700ms debounce fires.

- [ ] **Step 3: Implement immediate feedback** — change the `<input>` `oninput` (`sing.js:981`) to set loading before debouncing:

```js
    el("input", {
      type: "search",
      placeholder: "Type artist or song title…",
      autocomplete: "off",
      oninput: (e) => {
        state.query = e.target.value;
        // Immediate feedback: show the searching hint the moment a real query
        // is typed, before the 700ms debounce elapses (matches the KJ side).
        if (e.target.value.trim().length >= 3 && !loading) {
          loading = true; update();
        }
        doSearch(e.target.value);
      },
      value: state.query,
    }),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestSearchRace::test_typing_shows_searching_immediately -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "feat(sing): show Searching hint immediately on keystroke

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Stop the results list shifting under a finger (render-stability + tap cooldown)

Two guards: skip rebuilding the results node when nothing visibly changed, and make freshly-(re)built pick buttons inert for ~300ms so a tap aimed at the old layout can't fire a new row.

**Files:**
- Modify: `kj-controller/static-sing/sing.js` — `update()` (`sing.js:628-632`), `renderResults()` (`sing.js:907-970`), the CTA button onclick (`sing.js:939-942`) and the version-pick / toggle onclicks.
- Test: `kj-controller/tests/e2e/test_sing_frontend.py`

**Interfaces:**
- Consumes: closure vars `results`, `loading`, `err`.
- Produces: closure vars `lastResultsSig` (string), `armAt` (ms timestamp); helper `armed()`; constant `RESULT_ARM_MS = 300`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_identical_response_does_not_rebuild_results(self, page, live_server, live_token):
        """Re-searching the same query must not rebuild the results DOM (no shift)."""
        body = {"songs": [{"key": "q:1", "artist": "Queen", "title": "Bo Rhap",
                           "version_count": 1,
                           "versions": [{"source": "local",
                                         "local": {"path": "/x", "artist": "Queen",
                                                   "title": "Bo Rhap"}}]}]}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        self._goto_search(page, live_server, live_token)
        page.locator('input[type="search"]').fill("queen")
        expect(page.locator(".r-title")).to_have_text("Bo Rhap")
        build1 = page.locator(".results").get_attribute("data-build")
        # Trigger another identical search (same query text re-typed).
        page.evaluate("window.__sing_state.query = 'queen';")
        page.locator('input[type="search"]').fill("queen ")   # new keystroke, same trimmed query
        page.wait_for_timeout(900)
        build2 = page.locator(".results").get_attribute("data-build")
        assert build1 == build2, "results were rebuilt despite identical content"

    def test_pick_ignored_during_cooldown_then_works(self, page, live_server, live_token):
        """A tap within ~300ms of a (re)render is ignored; after it, it navigates."""
        body = {"songs": [{"key": "q:1", "artist": "Queen", "title": "Bo Rhap",
                           "version_count": 1,
                           "versions": [{"source": "local",
                                         "local": {"path": "/x", "artist": "Queen",
                                                   "title": "Bo Rhap"}}]}]}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        self._goto_search(page, live_server, live_token)
        page.locator('input[type="search"]').fill("queen")
        expect(page.locator(".btn-primary-cta")).to_be_visible()
        # Immediate tap (within cooldown) is swallowed — still on the search step.
        page.locator(".btn-primary-cta").click()
        assert page.evaluate("window.__sing_state.step") == "search"
        # After the cooldown, the same tap advances to confirm.
        page.wait_for_timeout(350)
        page.locator(".btn-primary-cta").click()
        expect(page.locator('[data-testid="confirm-song"], .pick-summary, .sing-card h2')).to_be_visible()
        assert page.evaluate("window.__sing_state.step") == "confirm"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestSearchRace -k "rebuild or cooldown" -v`
Expected: FAIL — no `data-build` attribute exists; the pick fires immediately (no cooldown).

- [ ] **Step 3: Add the render-stability signature + cooldown state** — at the top of `renderSearch()`, after the existing closure vars (near `sing.js:524`), add:

```js
  const RESULT_ARM_MS = 300;   // freshly-rendered rows are inert this long (anti-mis-tap)
  let lastResultsSig = null;   // signature of the currently-shown results
  let armAt = 0;               // pick buttons are inert until Date.now() >= armAt
  const armed = () => Date.now() >= armAt;
  const resultsSig = () =>
    [loading ? "L" : "", err,
     (results.songs || []).map((g) => `${g.key}:${g.version_count}`).join("|")].join("~");
```

- [ ] **Step 4: Make `update()` skip no-op rebuilds** — replace `update()` (`sing.js:628-632`):

```js
  function update() {
    const card = root.querySelector(".sing-card");
    const resultsEl = card?.querySelector(".results");
    if (!resultsEl) return;
    const sig = resultsSig();
    if (sig === lastResultsSig) return;   // nothing visibly changed — don't rebuild under a finger
    lastResultsSig = sig;
    resultsEl.replaceWith(renderResults());
  }
```

- [ ] **Step 5: Tag the results node + set the cooldown on every build, and guard the CTA** — in `renderResults()` (`sing.js:907-970`):
  1. change the container to carry a build counter, and arm the cooldown:

```js
  let resultsBuild = 0;   // add near the other closure vars at the top of renderSearch()
```

```js
  function renderResults() {
    const container = el("div", { class: "results", "data-build": String(++resultsBuild) });
    armAt = Date.now() + RESULT_ARM_MS;   // freshly-built rows are inert briefly
    if (loading) container.appendChild(el("p", { class: "hint" }, "Searching…"));
    if (err) container.appendChild(el("p", { class: "error" }, err));
    // ...rest unchanged...
```

  2. guard the primary CTA onclick (`sing.js:939-942`):

```js
        children.push(el("button", {
          class: "btn-primary-cta",
          onclick: (e) => { e.stopPropagation(); if (!armed()) return; onCtaClick(); },
        }, ctaLabel));
```

- [ ] **Step 6: Guard the version-pick and toggle onclicks** — apply the same `if (!armed()) return;` guard to the version "Pick this version →" button (`sing.js:748-751`) and the versions toggle (`sing.js:955-959`):

```js
      el("button", {
        class: "sing-version-pick",
        onclick: (e) => { e.stopPropagation(); if (!armed()) return; pickSpecificVersion(group, version); },
      }, "Pick this version →"),
```

```js
          children.push(el("button", {
            class: "sing-versions-toggle",
            "aria-expanded": isExpanded ? "true" : "false",
            onclick: (e) => { e.stopPropagation(); if (!armed()) return; toggleExpanded(group.key); },
          }, toggleLabel));
```

- [ ] **Step 7: Reset `lastResultsSig` so the initial card render registers** — in `renderSearch`, the card is built with an inline `renderResults()` at `sing.js:984`; set the baseline signature right after the card is created (just before `if (state.query) doSearch(state.query);` at `sing.js:996`):

```js
  lastResultsSig = resultsSig();
  if (state.query) doSearch(state.query);
  return card;
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestSearchRace -k "rebuild or cooldown" -v`
Expected: PASS.

- [ ] **Step 9: Run the full singer e2e file**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py -v`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "fix(sing): stop the results list shifting under a finger

Skip no-op result rebuilds and make freshly-rendered pick buttons inert for
300ms, so a tap aimed at the previous layout can't select a new row.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Harden the confirm screen

Make the song the dominant element, show the source explicitly, add a "you searched" breadcrumb, and use explicit action labels.

**Files:**
- Modify: `kj-controller/static-sing/sing.js` — `renderConfirm()` card return (`sing.js:1124-1136` region) and the `send()` error-reset label (`sing.js:1034`).
- Modify: `kj-controller/static-sing/sing.css` — add `.sing-confirm` / `.confirm-*` styles.
- Test: `kj-controller/tests/e2e/test_sing_frontend.py`

**Interfaces:**
- Consumes: `state.selected` (`song_title`, `song_artist`, `source_type`, `label`), `state.query`.
- Produces: helper `_confirmSourceLine(sel)`; DOM `[data-testid="confirm-song"]`, `.confirm-title`, `.confirm-artist`, `.confirm-source`, `.confirm-searched`. Keeps `.submit-btn` and `.error` (relied on by `send()`).

- [ ] **Step 1: Write the failing test**

```python
class TestConfirmHardening:
    def _confirm(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.evaluate("""
            window.__sing_state.query = 'bohemian';
            window.__sing_state.selected = {
                source_type: 'local', source_ref: '/x.mp4',
                song_artist: 'Queen', song_title: 'Bohemian Rhapsody',
                label: 'Bohemian Rhapsody — Queen (in library)',
            };
            window.__sing_state.step = 'confirm';
            window.__sing_render();
        """)

    def test_confirm_shows_song_source_and_breadcrumb(self, page, live_server, live_token):
        self._confirm(page, live_server, live_token)
        expect(page.locator(".confirm-title")).to_have_text("Bohemian Rhapsody")
        expect(page.locator(".confirm-artist")).to_have_text("Queen")
        expect(page.locator(".confirm-source")).to_have_text("In our library")
        expect(page.locator(".confirm-searched")).to_contain_text("bohemian")
        expect(page.locator(".submit-btn")).to_have_text("Yes — send to the KJ")
        expect(page.get_by_role("button", name="← Pick a different song")).to_be_visible()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestConfirmHardening -v`
Expected: FAIL — `.confirm-title`/`.confirm-source`/`.confirm-searched` don't exist; button text is "Send to KJ".

- [ ] **Step 3: Add the source-line helper** — inside `renderConfirm()`, above the `return el("main", ...)` (around `sing.js:1124`):

```js
  function _confirmSourceLine(sel) {
    switch (sel && sel.source_type) {
      case "local": return "In our library";
      case "divebar": return "Community karaoke (in our library)";
      case "kn": return "Online karaoke (download needed)";
      case "youtube": return "From a YouTube link";
      case "make": return "The KJ will make this for you";
      case "kj_pick": return "The KJ will pick the best version";
      default: return "";
    }
  }
```

- [ ] **Step 4: Replace the confirm card return** — replace the existing card return (`sing.js:1124-1139`), which currently reads `return el("main", { class: "sing-card" }, el("h2", {}, "Looking good?"), el("div", { class: "pick-summary" }, el("div", { class: "pick-label" }, ...)), el("p", { class: "hint" }, ...Your details...), renderPartnersSection(), el("div", { class: "row" }, ...Change... ...Send to KJ...), el("p", { class: "error" }, err))`, with:

```js
  const sel = state.selected || {};
  return el("main", { class: "sing-card sing-confirm" },
    el("h2", {}, "Is this the right song?"),
    el("div", { class: "confirm-song", "data-testid": "confirm-song" },
      el("div", { class: "confirm-title" }, sel.song_title || sel.label || ""),
      sel.song_artist ? el("div", { class: "confirm-artist" }, sel.song_artist) : null,
      el("div", { class: "confirm-source" }, _confirmSourceLine(sel)),
    ),
    state.query ? el("p", { class: "confirm-searched hint" }, `You searched: "${state.query}"`) : null,
    el("p", { class: "hint" },
      state.phone
        ? `Your details: ${state.name} · ${state.phone}`
        : `Your details: ${state.name}`),
    renderPartnersSection(),
    el("div", { class: "row confirm-actions" },
      el("button", { class: "btn ghost", onclick: back("search") }, "← Pick a different song"),
      el("button", { class: "btn primary submit-btn", onclick: send }, "Yes — send to the KJ"),
    ),
    el("p", { class: "error" }, err),   // keep ALWAYS present — send()'s inline error path sets its textContent
  );
```

Keep the existing `renderPartnersSection()` helper (`sing.js:1076-1122`) exactly as-is — do not drop or inline it. Keep the `.error` `<p>` unconditional (the partner-phone error path does `root.querySelector(".error").textContent = err`, so the element must exist even when `err` is empty).

- [ ] **Step 5: Fix the error-reset button label in `send()`** — `send()` resets the button text back to `"Send to KJ"` in **two** places: the partner-phone validation path (`sing.js:1034`) and the catch/finally reset (`sing.js:1069`). Change **both** occurrences of `submitBtn.textContent = "Send to KJ";` to:

```js
            submitBtn.textContent = "Yes — send to the KJ";
```

- [ ] **Step 6: Add confirm styles** — append to `kj-controller/static-sing/sing.css`:

```css
.sing-confirm .confirm-song { margin: 12px 0 8px; }
.sing-confirm .confirm-title { font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
.sing-confirm .confirm-artist { font-size: 1.05rem; opacity: 0.9; margin-top: 2px; }
.sing-confirm .confirm-source { font-size: 0.85rem; opacity: 0.7; margin-top: 6px; }
.sing-confirm .confirm-searched { font-size: 0.8rem; opacity: 0.6; margin: 4px 0 12px; }
.sing-confirm .confirm-actions { gap: 10px; }
.sing-confirm .confirm-actions .submit-btn { flex: 1; }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestConfirmHardening -v`
Expected: PASS.

- [ ] **Step 8: Run the full singer e2e file (partners tests must still pass)**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py -v`
Expected: all PASS (including `TestConfirmPartners`).

- [ ] **Step 9: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "feat(sing): harden the confirm screen against wrong-song submits

Song title/artist dominant, explicit source line, 'you searched' breadcrumb,
and clear 'Yes — send to the KJ' / 'Pick a different song' actions.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Version-list — collapse noisy commercial versions + Best/trusted markers

Port the KJ dropdown's collapse + lead markers (`app.js:7329-7372`, `rotLeadMark` `app.js:7528-7532`). The grouped `versions[]` already carry `priority_stated`/`priority_class`/`priority_rank` (`version_priority.py:342-345`) and are sorted best-first.

**Files:**
- Modify: `kj-controller/static-sing/sing.js` — `renderVersionsExpander()` (`sing.js:756-793`) and `renderVersionRow()` (`sing.js:697-754`).
- Modify: `kj-controller/static-sing/sing.css` — markers + collapse toggle styles.
- Test: `kj-controller/tests/e2e/test_sing_frontend.py`

**Interfaces:**
- Consumes: `group.versions[]` with fields `source`, `local`/`kn`, `priority_stated` (bool). `_versionSection(v)` (`sing.js:509-515`) → `library`|`divebar`|`online`|`community`. Closure `expandedSongs` and `toggleExpanded` already exist.
- Produces: DOM markers `.sing-version-best`, `.sing-version-star`; a per-group online-collapse toggle `.sing-online-toggle` with `data-testid="online-collapse-toggle"`.

- [ ] **Step 1: Write the failing tests** — these drive the search results directly via a stubbed multi-version response:

```python
class TestVersionList:
    def _multi(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        # 1 community + 3 commercial-online versions; best-first order preserved.
        body = {"songs": [{
            "key": "q:multi", "artist": "Queen", "title": "Bo Rhap",
            "version_count": 4, "in_library": False,
            "versions": [
                {"source": "kn", "priority_stated": True,
                 "kn": {"brand_name": "SongService", "is_community": True}},
                {"source": "kn", "priority_stated": False,
                 "kn": {"brand_name": "BrandA"}},
                {"source": "kn", "priority_stated": False,
                 "kn": {"brand_name": "BrandB"}},
                {"source": "kn", "priority_stated": False,
                 "kn": {"brand_name": "BrandC"}},
            ]}]}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.locator('input[type="search"]').fill("bo rhap")
        expect(page.locator(".result-row")).to_be_visible()
        page.wait_for_timeout(350)  # clear the render cooldown before interacting
        # open the versions expander
        page.locator(".sing-versions-toggle").click()

    def test_best_marker_on_first_version(self, page, live_server, live_token):
        self._multi(page, live_server, live_token)
        expect(page.locator(".sing-version-best").first).to_be_visible()

    def test_noisy_commercial_collapsed_when_good_option_present(self, page, live_server, live_token):
        self._multi(page, live_server, live_token)
        # The 3 commercial-online versions are hidden behind a toggle.
        expect(page.locator('[data-testid="online-collapse-toggle"]')).to_be_visible()
        expect(page.locator('.sing-version-section[data-section="online"] .sing-version-card')).to_have_count(0)
        page.locator('[data-testid="online-collapse-toggle"]').click()
        expect(page.locator('.sing-version-section[data-section="online"] .sing-version-card')).to_have_count(3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestVersionList -v`
Expected: FAIL — no `.sing-version-best`, no online-collapse toggle (all 3 render).

- [ ] **Step 3: Add the Best/trusted markers to `renderVersionRow()`** — `renderVersionRow(group, version)` needs to know if it is the best row. Change its signature to accept an `isBest` flag and render markers. Update the call sites in Step 4. In `renderVersionRow` (`sing.js:697`), change the signature and prepend a marker to the primary line:

```js
  function renderVersionRow(group, version, isBest) {
    let icon, primary, secondary = "", pathBlock = null;
    // ...unchanged body that computes icon/primary/secondary/pathBlock...
    const mark = isBest
      ? el("span", { class: "sing-version-best", title: "Best available version" }, "Best")
      : (version.priority_stated
          ? el("span", { class: "sing-version-star", title: "Reliably high-quality brand" }, "⭐")
          : null);
    const card = el("div", { class: "sing-version-card" },
      el("div", { class: "sing-version-icon" }, icon),
      el("div", { class: "sing-version-main" },
        el("div", { class: "sing-version-primary" }, mark, mark ? " " : null, primary),
        secondary ? el("div", { class: "sing-version-secondary" }, secondary) : null,
        pathBlock,
      ),
      el("button", {
        class: "sing-version-pick",
        onclick: (e) => { e.stopPropagation(); if (!armed()) return; pickSpecificVersion(group, version); },
      }, "Pick this version →"),
    );
    return card;
  }
```

- [ ] **Step 4: Collapse the online section + pass `isBest`** — replace the section-render loop in `renderVersionsExpander()` (`sing.js:784-792`) with logic that (a) marks the single best version overall, and (b) collapses the `online` section behind a toggle when a good option (library/divebar/community) exists:

```js
    // The overall best version is versions[0] (backend sorts best-first).
    const bestVersion = (group.versions || [])[0] || null;
    const hasGoodOption = ["library", "divebar", "community"].some((k) => byKey[k].length);

    for (const { key, label } of sections) {
      const versions = byKey[key];
      if (!versions.length) continue;
      const section = el("div", { class: "sing-version-section", "data-section": key },
        el("h4", {}, label),
      );
      // Collapse noisy commercial-online downloads when a good option is already shown.
      const collapseThis = key === "online" && hasGoodOption && !expandedSongs.has(`${group.key}::online`);
      if (collapseThis) {
        section.appendChild(el("button", {
          class: "sing-online-toggle",
          "data-testid": "online-collapse-toggle",
          onclick: (e) => {
            e.stopPropagation();
            if (!armed()) return;
            expandedSongs.add(`${group.key}::online`);
            update();
          },
        }, `▸ ${versions.length} more online version${versions.length === 1 ? "" : "s"} (download needed)`));
      } else {
        for (const v of versions) section.appendChild(renderVersionRow(group, v, v === bestVersion));
      }
      wrapper.appendChild(section);
    }
    return wrapper;
```

- [ ] **Step 5: Add marker + toggle styles** — append to `kj-controller/static-sing/sing.css`:

```css
.sing-version-best { display: inline-block; font-size: 0.7rem; font-weight: 700;
  background: #ffce54; color: #1a1a1a; border-radius: 4px; padding: 1px 6px; }
.sing-version-star { font-size: 0.85rem; }
.sing-online-toggle { display: block; width: 100%; text-align: left; background: none;
  border: 1px dashed rgba(255,255,255,0.25); color: inherit; opacity: 0.75;
  border-radius: 8px; padding: 8px 10px; margin-top: 6px; cursor: pointer; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestVersionList -v`
Expected: PASS.

- [ ] **Step 7: Run the full singer e2e file**

Run: `cd kj-controller && pytest tests/e2e/test_sing_frontend.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "feat(sing): mark Best/trusted versions and collapse noisy commercial ones

Port the KJ link-search version presentation to the singer UI: a Best pill on
the top version, trusted-brand star, and commercial-online downloads collapsed
behind a toggle when a good option is already visible. Fewer, clearer targets.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Version bump for cache-bust

Frontend asset changes only take effect on singers' phones when `APP_VERSION` changes (assets are `?v={{ config.get('APP_VERSION') }}`; the service worker keys its cache on `__APP_VERSION__`).

**Files:**
- Modify: `kj-controller/pyproject.toml` (version field)

- [ ] **Step 1: Find the current version**

Run: `grep -n '^version' kj-controller/pyproject.toml`
Expected: prints the current `version = "0.7x.y"` line.

- [ ] **Step 2: Bump the minor version** — edit `kj-controller/pyproject.toml`, incrementing the minor version (e.g. `0.75.0` → `0.76.0`) since this PR is a user-facing feature set.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/pyproject.toml
git commit -m "chore(sing): bump version for search-reliability frontend deploy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** A1 (Task 1), A2 (Tasks 1+2), A3 (Task 3), A4 (Task 4), A5 (Task 5), cache-bust/version (Task 6). The A5 "do not reintroduce `config` shadow" is a Global Constraint (no code change needed). No backend change required — all `priority_*` metadata already present.
- **Type consistency:** `renderVersionRow` gains an `isBest` third arg (updated at its only call site in Task 5's loop). `armed()` / `RESULT_ARM_MS` / `armAt` defined in Task 3 and consumed by Tasks 3 and 5 (version-pick + online-toggle onclicks). `resultsSig`/`lastResultsSig`/`resultsBuild` all defined in Task 3.
- **Deploy:** frontend-only; auto-deploy pulls without a service restart (no playback interruption). Still requires push permission per `CLAUDE.md`.
