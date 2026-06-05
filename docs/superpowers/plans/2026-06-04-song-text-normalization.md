# Unified Song-Text Normalization & Fuzzy Matching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one shared text-normalization module used by every song-text search, fixing the `and`/`&` bug-class and adding fuzzy typo tolerance, backed by a real-data-derived test corpus + metrics harness.

**Architecture:** A pure-Python `text_normalize` module is the single source of truth for canonicalization (Unicode/diacritics, `&`↔`and`, numbers↔words, `feat.`, abbreviations). A JS twin (`text_normalize.js`) mirrors it and is verified by a node-driven parity test over a shared corpus. The catalog's FTS5 index and every search call site route through the module; a `rapidfuzz` fallback over a trigram-narrowed candidate pool handles typos. Changing index-time normalization requires a one-time `media_fts` rebuild gated by a `NORMALIZER_VERSION` stamp.

**Tech Stack:** Python 3, Flask, SQLite FTS5 (`unicode61` + new `trigram` index), `rapidfuzz`, vanilla JS (no build step), pytest, node (test-only, for JS parity).

**Spec:** `docs/archive/2026-06-04-song-text-normalization-design.md`

**Working dir for all commands:** `kj-controller/` inside the worktree `/Users/andrew/Projects/nomadkaraoke/kjbox-song-normalization`. Run pytest from `kj-controller/`.

---

## File Structure

**Create:**
- `kj-controller/text_normalize.py` — core canonicalizer (constants + `normalize`/`tokens`/`fts_match_query`/`group_key`)
- `kj-controller/static/text_normalize.js` — JS twin, UMD factory `makeNormalizer(maps)` (browser + node)
- `kj-controller/scripts/reindex_catalog.py` — CLI to rebuild `media_fts` + trigram index with current normalizer
- `kj-controller/scripts/search_metrics.py` — recall@K / precision harness over the corpus
- `kj-controller/tests/search_corpus.py` — deterministic messy-variant generator + real-sample loader
- `kj-controller/tests/unit/test_text_normalize.py` — golden unit tests for the module
- `kj-controller/tests/unit/test_text_normalize_js_parity.py` — node-driven JS↔Python parity over the corpus
- `kj-controller/tests/integration/test_search_corpus.py` — end-to-end recall tests against a temp catalog
- `kj-controller/tests/fixtures/real_rotation_samples.json` — committed real-world regression samples (may start empty `[]`)

**Modify:**
- `kj-controller/requirements.txt` — add `rapidfuzz`
- `kj-controller/catalog.py` — delegate normalization to `text_normalize`; add trigram index, `NORMALIZER_VERSION` metadata, `rebuild_fts()`, fuzzy fallback in `search()`
- `kj-controller/routes.py` — `_normalize_song_key`→`group_key`; `unified_search` local filter via `tokens`; inject maps into template; tidy external-engine queries
- `kj-controller/templates/index.html` — inject `abbrevMap`/`numberWords`/`romanMap`/`normalizerVersion` into `window.KJ_CONFIG`; `<script>` include `text_normalize.js`
- `kj-controller/static/app.js` — build normalizer from `text_normalize.js` + injected maps
- `kj-controller/tests/unit/test_catalog.py` — update normalization tests to unified (lowercased) output; replace Python-mirror parity test with the node parity test
- `kj-controller/docs/CHANGELOG.md` + `kj-controller/docs/ARCHITECTURE.md` — document module + reindex deploy step

---

## Phase 0 — Dependency

### Task 0: Add rapidfuzz

**Files:**
- Modify: `kj-controller/requirements.txt`
- Test: `kj-controller/tests/unit/test_text_normalize.py`

- [ ] **Step 1: Add the dependency**

Add this line to `kj-controller/requirements.txt` (keep alphabetical-ish grouping, place after `qrcode`):

```
rapidfuzz>=3.0
```

- [ ] **Step 2: Install into the active environment**

Run: `pip install 'rapidfuzz>=3.0'`
Expected: ends with `Successfully installed rapidfuzz-...`

- [ ] **Step 3: Smoke-test import**

Create `kj-controller/tests/unit/test_text_normalize.py` with only:

```python
def test_rapidfuzz_importable():
    from rapidfuzz import fuzz
    assert fuzz.WRatio("simon and garfunkel", "simon & garfunkel") > 80
```

- [ ] **Step 4: Run it**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/requirements.txt kj-controller/tests/unit/test_text_normalize.py
git commit -m "build: add rapidfuzz dependency for fuzzy song matching"
```

---

## Phase 1 — Core `text_normalize.py` (TDD)

> Build the canonicalizer one transform at a time. Each transform: failing test → implement → pass → commit.

### Task 1: Module skeleton + constants + diacritic fold

**Files:**
- Create: `kj-controller/text_normalize.py`
- Test: `kj-controller/tests/unit/test_text_normalize.py`

- [ ] **Step 1: Write failing tests** (append to `tests/unit/test_text_normalize.py`)

```python
from text_normalize import (
    normalize, tokens, fts_match_query, group_key,
    LATIN_SPECIAL_MAP, ABBREV_MAP, NUMBER_WORDS, ROMAN_NUMERALS,
    NORMALIZER_VERSION,
)


class TestDiacritics:
    def test_acute_lowercased(self):
        assert normalize("Beyoncé") == "beyonce"

    def test_o_stroke(self):
        assert normalize("MØ") == "mo"

    def test_sharp_s(self):
        assert normalize("Straßenbande") == "strassenbande"

    def test_empty(self):
        assert normalize("") == ""

    def test_none_safe(self):
        assert normalize(None) == ""

    def test_version_is_int(self):
        assert isinstance(NORMALIZER_VERSION, int) and NORMALIZER_VERSION >= 1
```

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestDiacritics -v`
Expected: FAIL (ModuleNotFoundError: text_normalize)

- [ ] **Step 3: Create the module**

Create `kj-controller/text_normalize.py`:

```python
"""Single source of truth for song-text normalization.

Pure functions (no Flask / SQLite). The same pipeline is applied to indexed
catalog text AND to user queries so both meet in one canonical space. A JS twin
(static/text_normalize.js) mirrors this exactly and is verified by a parity test.

Pipeline order (order matters):
  1. Unicode NFD + strip combining marks + Latin special-char fold
  2. lowercase
  3. strip feat./ft./featuring qualifiers
  4. expand symbol conjunctions  &  +  -> " and "
  5. drop apostrophes; replace remaining punctuation with space
  6. token-level word abbreviations (pt->part, vs->versus, ...)
  7. number canonicalization: words/roman -> digits, with tens+ones adjacency
"""

import re
import unicodedata

# Bump whenever the pipeline or any map changes; gates catalog reindex.
NORMALIZER_VERSION = 1

# Non-decomposable Latin chars that NFD cannot handle.
LATIN_SPECIAL_MAP = {
    'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'ß': 'ss',
    'ð': 'd', 'Ð': 'd', 'ł': 'l', 'Ł': 'l', 'ı': 'i',
    'đ': 'd', 'Đ': 'd', 'þ': 'th', 'Þ': 'th',
}
_LATIN_SPECIAL_RE = re.compile(
    '[' + re.escape(''.join(LATIN_SPECIAL_MAP)) + ']'
)


def _strip_accents(text):
    s = unicodedata.normalize('NFD', text)
    s = re.sub(r'[̀-ͯ]', '', s)
    s = _LATIN_SPECIAL_RE.sub(lambda m: LATIN_SPECIAL_MAP[m.group()], s)
    return s


def normalize(text):
    """Return the canonical, space-joined token string for `text`."""
    if not text:
        return ""
    s = _strip_accents(text)
    s = s.lower()
    return s
```

- [ ] **Step 4: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestDiacritics -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/text_normalize.py kj-controller/tests/unit/test_text_normalize.py
git commit -m "feat(normalize): module skeleton with diacritic/Latin fold + lowercase"
```

### Task 2: feat-stripping + symbol conjunction + punctuation/apostrophes + tokens()

**Files:**
- Modify: `kj-controller/text_normalize.py`
- Test: `kj-controller/tests/unit/test_text_normalize.py`

- [ ] **Step 1: Write failing tests** (append)

```python
class TestPunctAndFeat:
    def test_ampersand_to_and(self):
        assert normalize("Simon & Garfunkel") == "simon and garfunkel"

    def test_plus_to_and(self):
        assert normalize("Florence + The Machine") == "florence and the machine"

    def test_apostrophe_dropped(self):
        assert normalize("Don't Stop") == "dont stop"

    def test_punct_to_space(self):
        assert normalize("Hello, World! (x)") == "hello world x"

    def test_feat_stripped(self):
        assert normalize("Stay feat. Justin Bieber") == "stay"

    def test_ft_stripped(self):
        assert normalize("Title ft. Someone Else") == "title"

    def test_featuring_stripped(self):
        assert normalize("Song featuring The Band") == "song"

    def test_tokens(self):
        assert tokens("Simon & Garfunkel") == ["simon", "and", "garfunkel"]

    def test_tokens_empty(self):
        assert tokens("") == []
```

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestPunctAndFeat -v`
Expected: FAIL (`&` not converted; `tokens` undefined)

- [ ] **Step 3: Extend the module**

Add to `text_normalize.py` (above `normalize`), and update `normalize`, then add `tokens`:

```python
# feat./ft./featuring qualifier; stops at a closing bracket or end of string.
_FEAT_RE = re.compile(
    r'\s*[\[(]?\s*(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]?',
    re.IGNORECASE,
)
_SYMBOL_AND_RE = re.compile(r'\s*[&+]\s*')
_APOS_RE = re.compile(r"[’'ʼ‘`]+")
_PUNCT_RE = re.compile(r'[^\w\s]', re.UNICODE)
```

Replace the body of `normalize` with:

```python
def normalize(text):
    """Return the canonical, space-joined token string for `text`."""
    if not text:
        return ""
    s = _strip_accents(text)
    s = s.lower()
    s = _FEAT_RE.sub(' ', s)
    s = _SYMBOL_AND_RE.sub(' and ', s)
    s = _APOS_RE.sub('', s)
    s = _PUNCT_RE.sub(' ', s)
    toks = s.split()
    return ' '.join(toks)


def tokens(text):
    """Return the canonical token list for `text`."""
    n = normalize(text)
    return n.split() if n else []
```

- [ ] **Step 4: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestPunctAndFeat -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/text_normalize.py kj-controller/tests/unit/test_text_normalize.py
git commit -m "feat(normalize): feat-strip, &/+ -> and, apostrophe/punct handling, tokens()"
```

### Task 3: Word abbreviations + number/roman canonicalization

**Files:**
- Modify: `kj-controller/text_normalize.py`
- Test: `kj-controller/tests/unit/test_text_normalize.py`

- [ ] **Step 1: Write failing tests** (append)

```python
class TestAbbrevAndNumbers:
    def test_pt_to_part(self):
        assert normalize("Song Pt 2") == "song part 2"

    def test_vs_to_versus(self):
        assert normalize("A vs B") == "a versus b"

    def test_word_to_digit(self):
        assert normalize("Two Princes") == "2 princes"

    def test_digit_passthrough(self):
        assert normalize("Blink 182") == "blink 182"

    def test_tens_ones_combine(self):
        assert normalize("Twenty One Pilots") == "21 pilots"

    def test_plain_tens(self):
        assert normalize("Thirty Seconds To Mars") == "30 seconds to mars"

    def test_roman_safe(self):
        assert normalize("Rocky IV") == "rocky 4"

    def test_ambiguous_roman_left_alone(self):
        # single 'i', 'v', 'x' are too ambiguous to remap
        assert normalize("I Will Survive") == "i will survive"

    def test_maps_exposed(self):
        assert ABBREV_MAP["pt"] == "part"
        assert NUMBER_WORDS["two"] == 2
        assert ROMAN_NUMERALS["iv"] == 4
```

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestAbbrevAndNumbers -v`
Expected: FAIL

- [ ] **Step 3: Extend the module**

Add the maps + helper, and call it from `normalize`:

```python
# Conservative whole-token abbreviations (avoid ambiguous single letters).
ABBREV_MAP = {
    "pt": "part", "pts": "parts",
    "vs": "versus",
}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}

# Exclude ambiguous standalone i/v/x/l/c/d/m.
ROMAN_NUMERALS = {
    "ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
}

_TENS = {20, 30, 40, 50, 60, 70, 80, 90}


def _canonicalize_numbers(toks):
    out = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        val = NUMBER_WORDS.get(t)
        if val is None:
            val = ROMAN_NUMERALS.get(t)
        if val is None:
            out.append(t)
            i += 1
            continue
        # tens + ones adjacency: "twenty one" -> 21
        if val in _TENS and i + 1 < n:
            ones = NUMBER_WORDS.get(toks[i + 1])
            if ones is not None and 1 <= ones <= 9:
                val += ones
                i += 1
        out.append(str(val))
        i += 1
    return out
```

In `normalize`, replace the final two lines (`toks = s.split()` / `return ' '.join(toks)`) with:

```python
    toks = s.split()
    toks = [ABBREV_MAP.get(t, t) for t in toks]
    toks = _canonicalize_numbers(toks)
    return ' '.join(toks)
```

- [ ] **Step 4: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestAbbrevAndNumbers -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/text_normalize.py kj-controller/tests/unit/test_text_normalize.py
git commit -m "feat(normalize): word abbreviations + number/roman canonicalization"
```

### Task 4: `fts_match_query` + `group_key`

**Files:**
- Modify: `kj-controller/text_normalize.py`
- Test: `kj-controller/tests/unit/test_text_normalize.py`

- [ ] **Step 1: Write failing tests** (append)

```python
class TestQueryAndGroupKey:
    def test_fts_quotes_and_prefixes_last(self):
        assert fts_match_query("bon jovi livin") == '"bon" "jovi" "livin"*'

    def test_fts_uses_canonical_tokens(self):
        # "&" -> "and" so the index (also canonical) matches
        assert fts_match_query("Simon & Garfunkel") == '"simon" "and" "garfunkel"*'

    def test_fts_empty(self):
        assert fts_match_query("   ") == ""

    def test_group_key(self):
        assert group_key("Simon & Garfunkel", "The Sound of Silence") == \
            "simon and garfunkel|||the sound of silence"

    def test_group_key_none_safe(self):
        assert group_key(None, None) == "|||"
```

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestQueryAndGroupKey -v`
Expected: FAIL

- [ ] **Step 3: Implement** (append to `text_normalize.py`)

```python
def fts_match_query(text):
    """Build an FTS5-safe MATCH string from canonical tokens.

    Each term quoted; last term prefix-matched. "" if no tokens.
    """
    toks = tokens(text)
    if not toks:
        return ""
    quoted = [f'"{t}"' for t in toks[:-1]]
    quoted.append(f'"{toks[-1]}"*')
    return ' '.join(quoted)


def group_key(artist, title):
    """Deterministic (artist, title) -> collapse key for grouping results."""
    return f"{normalize(artist)}|||{normalize(title)}"
```

- [ ] **Step 4: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py::TestQueryAndGroupKey -v`
Expected: PASS

- [ ] **Step 5: Run the whole module test file**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add kj-controller/text_normalize.py kj-controller/tests/unit/test_text_normalize.py
git commit -m "feat(normalize): fts_match_query + group_key helpers"
```

---

## Phase 2 — JS twin + parity + frontend wiring

### Task 5: `text_normalize.js` UMD factory

**Files:**
- Create: `kj-controller/static/text_normalize.js`
- Test: (covered by Task 6 parity test)

- [ ] **Step 1: Create the JS twin**

Create `kj-controller/static/text_normalize.js`. It must mirror the Python pipeline exactly. Maps are passed in (from `window.KJ_CONFIG` in the browser, or from the Python constants in the node test):

```javascript
// Mirror of text_normalize.py. Keep in lockstep; parity is enforced by
// tests/unit/test_text_normalize_js_parity.py (runs this file under node).
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.TextNormalize = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {

  function makeNormalizer(maps) {
    const latin = maps.latinSpecialMap || {};
    const abbrev = maps.abbrevMap || {};
    const numberWords = maps.numberWords || {};   // word -> int
    const roman = maps.romanMap || {};            // roman -> int
    const tens = new Set([20, 30, 40, 50, 60, 70, 80, 90]);

    const latinKeys = Object.keys(latin);
    const latinRe = latinKeys.length
      ? new RegExp('[' + latinKeys.join('').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ']', 'g')
      : null;
    const featRe = /\s*[\[(]?\s*(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]?/ig;
    const symbolAndRe = /\s*[&+]\s*/g;
    const aposRe = /[’'ʼ‘`]+/g;
    const punctRe = /[^\w\s]/gu;

    function canonNumbers(toks) {
      const out = [];
      for (let i = 0; i < toks.length; i++) {
        const t = toks[i];
        let val = (t in numberWords) ? numberWords[t]
                : (t in roman) ? roman[t] : null;
        if (val === null) { out.push(t); continue; }
        if (tens.has(val) && i + 1 < toks.length && (toks[i + 1] in numberWords)) {
          const ones = numberWords[toks[i + 1]];
          if (ones >= 1 && ones <= 9) { val += ones; i++; }
        }
        out.push(String(val));
      }
      return out;
    }

    function normalize(text) {
      if (!text) return '';
      let s = text.normalize('NFD').replace(/[̀-ͯ]/g, '');
      if (latinRe) s = s.replace(latinRe, m => latin[m]);
      s = s.toLowerCase();
      s = s.replace(featRe, ' ');
      s = s.replace(symbolAndRe, ' and ');
      s = s.replace(aposRe, '');
      s = s.replace(punctRe, ' ');
      let toks = s.split(/\s+/).filter(Boolean);
      toks = toks.map(t => (t in abbrev) ? abbrev[t] : t);
      toks = canonNumbers(toks);
      return toks.join(' ');
    }

    function tokens(text) {
      const n = normalize(text);
      return n ? n.split(' ') : [];
    }

    return { normalize, tokens };
  }

  return { makeNormalizer };
}));
```

> Note: Python folds the Latin map then lowercases; JS folds then lowercases too. Latin map values here are already lowercase, so order matches. The parity test is the real guarantee.

- [ ] **Step 2: Syntax check**

Run: `node -e "require('./kj-controller/static/text_normalize.js'); console.log('ok')"`
Expected: prints `ok`

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/text_normalize.js
git commit -m "feat(normalize): JS twin (UMD factory) mirroring text_normalize.py"
```

### Task 6: Node-driven JS↔Python parity test

**Files:**
- Create: `kj-controller/tests/unit/test_text_normalize_js_parity.py`
- Test: itself

- [ ] **Step 1: Write the parity test**

Create `kj-controller/tests/unit/test_text_normalize_js_parity.py`:

```python
"""Run the REAL static/text_normalize.js under node over a shared corpus and
assert byte-identical output with the Python normalizer."""
import json
import os
import shutil
import subprocess
import pytest

import text_normalize as tn

HERE = os.path.dirname(__file__)
JS_FILE = os.path.abspath(os.path.join(HERE, "..", "..", "static", "text_normalize.js"))

CASES = [
    "Simon & Garfunkel", "Don't Stop Believin'", "Beyoncé", "MØ",
    "Straßenbande", "Twenty One Pilots", "Thirty Seconds To Mars",
    "Blink 182", "Two Princes", "Rocky IV", "Stay feat. Justin Bieber",
    "Florence + The Machine", "Song Pt 2", "A vs B", "I Will Survive",
    "187 Straßenbande - Millionär", "The Sound of Silence", "", "Queen",
]


def _maps():
    return {
        "latinSpecialMap": tn.LATIN_SPECIAL_MAP,
        "abbrevMap": tn.ABBREV_MAP,
        "numberWords": tn.NUMBER_WORDS,
        "romanMap": tn.ROMAN_NUMERALS,
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_matches_python():
    script = (
        "const tn=require(process.argv[1]);"
        "const maps=JSON.parse(process.argv[2]);"
        "const cases=JSON.parse(process.argv[3]);"
        "const n=tn.makeNormalizer(maps);"
        "console.log(JSON.stringify(cases.map(c=>n.normalize(c))));"
    )
    out = subprocess.check_output(
        ["node", "-e", script, JS_FILE, json.dumps(_maps()), json.dumps(CASES)],
        text=True,
    )
    js_results = json.loads(out)
    py_results = [tn.normalize(c) for c in CASES]
    assert js_results == py_results
```

- [ ] **Step 2: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_text_normalize_js_parity.py -v`
Expected: PASS (if it fails, fix `text_normalize.js` until output matches Python — Python is authoritative)

- [ ] **Step 3: Commit**

```bash
git add kj-controller/tests/unit/test_text_normalize_js_parity.py
git commit -m "test(normalize): node-driven JS<->Python parity over shared corpus"
```

### Task 7: Inject maps into template + wire app.js

**Files:**
- Modify: `kj-controller/routes.py` (the `index()` view), `kj-controller/templates/index.html`, `kj-controller/static/app.js`
- Test: `kj-controller/tests/unit/test_catalog.py` (update injection test)

- [ ] **Step 1: Pass maps to the template**

In `routes.py`, find the `index()` view (currently `render_template('index.html', latin_special_map=LATIN_SPECIAL_MAP, config=cfg)`). Replace `LATIN_SPECIAL_MAP` usage with the `text_normalize` constants and pass all maps:

```python
import text_normalize as text_normalize
...
@routes_bp.route('/')
def index():
    """Serves the main remote control page."""
    cfg = current_app.kj_config
    return render_template(
        'index.html',
        latin_special_map=text_normalize.LATIN_SPECIAL_MAP,
        abbrev_map=text_normalize.ABBREV_MAP,
        number_words=text_normalize.NUMBER_WORDS,
        roman_map=text_normalize.ROMAN_NUMERALS,
        normalizer_version=text_normalize.NORMALIZER_VERSION,
        config=cfg,
    )
```

- [ ] **Step 2: Render maps into `window.KJ_CONFIG`**

In `templates/index.html`, find the inline script that sets `window.KJ_CONFIG = { latinSpecialMap: {{ latin_special_map | tojson }}, ... }`. Add the new keys and include the JS file. The KJ_CONFIG block becomes:

```html
    <script src="{{ url_for('static', filename='text_normalize.js') }}"></script>
    <script>
      window.KJ_CONFIG = Object.assign(window.KJ_CONFIG || {}, {
        latinSpecialMap: {{ latin_special_map | tojson }},
        abbrevMap: {{ abbrev_map | tojson }},
        numberWords: {{ number_words | tojson }},
        romanMap: {{ roman_map | tojson }},
        normalizerVersion: {{ normalizer_version | tojson }}
      });
    </script>
```

(Place the `<script src=...text_normalize.js>` BEFORE `app.js` is loaded. Keep any existing KJ_CONFIG keys — the `Object.assign` preserves them.)

- [ ] **Step 3: Use the twin in app.js**

In `static/app.js`, replace the block (around the current `normalizeForSearch`):

```javascript
// Character map injected from server (see index.html inline script)
const _latinSpecialMap = window.KJ_CONFIG.latinSpecialMap;
const _latinSpecialRe = new RegExp('[' + Object.keys(_latinSpecialMap).join('').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ']', 'g');

function normalizeForSearch(str) {
    // NFD decompose + strip combining marks (handles e with accent -> e, etc.)
    let s = str.normalize('NFD').replace(/[̀-ͯ]/g, '');
    // Handle non-decomposable Latin characters (ø→o, æ→ae, ß→ss, etc.)
    s = s.replace(_latinSpecialRe, m => _latinSpecialMap[m]);
    return s;
}
```

with:

```javascript
// Shared normalizer (mirror of text_normalize.py); see static/text_normalize.js
const _normalizer = TextNormalize.makeNormalizer(window.KJ_CONFIG);

function normalizeForSearch(str) {
    return _normalizer.normalize(str || '');
}
```

> `filterLocalMedia` already calls `normalizeForSearch(query.toLowerCase())` and on item text — that still works (normalize is idempotent w.r.t. lowercasing). Leave `filterLocalMedia` as-is.

- [ ] **Step 4: Update the injection test in test_catalog.py**

In `tests/unit/test_catalog.py`, replace `test_map_injected_to_template` so it checks the new keys (the old regex grabbed `latinSpecialMap`; now assert all four maps + version are present):

```python
    def test_maps_injected_to_template(self, flask_test_client):
        """Normalizer maps + version are rendered into the page."""
        response = flask_test_client.get('/')
        html = response.data.decode('utf-8')
        assert 'latinSpecialMap:' in html
        assert 'abbrevMap:' in html
        assert 'numberWords:' in html
        assert 'romanMap:' in html
        assert 'normalizerVersion:' in html
```

(Delete the old `test_map_injected_to_template` body it replaces.)

- [ ] **Step 5: Run affected tests**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py -k "inject" -v`
Expected: PASS

- [ ] **Step 6: Syntax-check app.js**

Run: `node --check kj-controller/static/app.js`
Expected: no output (valid)

- [ ] **Step 7: Commit**

```bash
git add kj-controller/routes.py kj-controller/templates/index.html kj-controller/static/app.js kj-controller/tests/unit/test_catalog.py
git commit -m "feat(normalize): inject maps + use shared JS normalizer in app.js"
```

---

## Phase 3 — Catalog integration + reindex

### Task 8: Delegate catalog normalization to the shared module

**Files:**
- Modify: `kj-controller/catalog.py`
- Test: `kj-controller/tests/unit/test_catalog.py`

- [ ] **Step 1: Update tests to unified (lowercased) output**

In `tests/unit/test_catalog.py`, the `TestNormalizeForSearch` class asserts diacritic-only output WITHOUT lowercasing (e.g. `_normalize_for_search("Beyoncé") == "Beyonce"`). Update each assertion to expect the unified lowercased canonical form. Representative replacements:

```python
    def test_acute(self):
        assert _normalize_for_search("Beyoncé") == "beyonce"

    def test_o_stroke(self):
        assert _normalize_for_search("MØ") == "mo"

    def test_ae_ligature(self):
        assert _normalize_for_search("Ænima") == "aenima"

    def test_sharp_s(self):
        assert _normalize_for_search("Straßenbande") == "strassenbande"

    def test_ascii_passthrough(self):
        assert _normalize_for_search("Bon Jovi") == "bon jovi"
```

Apply the same lowercase change to every assertion in `TestNormalizeForSearch`. Delete the `TestNormalizationConsistency._js_normalize` Python-mirror class entirely (the node parity test from Task 6 replaces it) — but KEEP `test_maps_injected_to_template` (already updated in Task 7; move it into `TestExternalCatalog` or a small standalone class if needed so it isn't orphaned).

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py::TestNormalizeForSearch -v`
Expected: FAIL (catalog still returns non-lowercased / non-canonical output)

- [ ] **Step 3: Delegate in catalog.py**

In `catalog.py`, replace the local `_normalize_for_search`, `_fts5_safe_query`, and the `LATIN_SPECIAL_MAP`/`LATIN_SPECIAL_MAP_RE` definitions with imports from the shared module. At the top of `catalog.py`:

```python
from text_normalize import (
    normalize as _normalize_for_search,
    fts_match_query as _fts5_safe_query,
    tokens as _query_tokens,
    LATIN_SPECIAL_MAP,  # re-export for any external importers
)
```

Then DELETE the old in-file definitions of `_normalize_for_search`, `_fts5_safe_query`, `LATIN_SPECIAL_MAP`, and `LATIN_SPECIAL_MAP_RE`. Update `_like_fallback` to derive terms from the shared tokenizer instead of its own regex:

```python
    def _like_fallback(self, query, limit, offset):
        """Fallback search using LIKE with punctuation stripped."""
        terms = _query_tokens(query)
        if not terms:
            return []
```

(Leave the rest of `_like_fallback` unchanged — it already lowercases via SQLite.)

> `_flush_batch` already calls `_normalize_for_search(...)` on artist/title/disc_id at index time — now it routes through the shared canonicalizer automatically. No change needed there beyond the import.

- [ ] **Step 4: Run catalog tests**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py -v`
Expected: PASS (update any remaining `_fts5_safe_query` expectations in `TestFts5SafeQuery` — they should now reflect canonical tokens, e.g. multi-term still `"a" "b"*`; these were already token-based so should pass unchanged)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/catalog.py kj-controller/tests/unit/test_catalog.py
git commit -m "refactor(catalog): delegate all normalization to shared text_normalize"
```

### Task 9: Catalog metadata + NORMALIZER_VERSION stamp

**Files:**
- Modify: `kj-controller/catalog.py`
- Test: `kj-controller/tests/unit/test_catalog.py`

- [ ] **Step 1: Write failing tests** (append to `TestExternalCatalog` or new class)

```python
class TestCatalogMeta:
    def test_version_stamped_on_build(self, tmp_path):
        from text_normalize import NORMALIZER_VERSION
        cat = _build_tiny_catalog(tmp_path, [
            "/m/KK-1 - Simon & Garfunkel - Sound Of Silence.zip",
        ])
        assert cat.normalizer_version() == NORMALIZER_VERSION

    def test_is_stale_false_after_build(self, tmp_path):
        cat = _build_tiny_catalog(tmp_path, [
            "/m/KK-1 - Queen - Bohemian Rhapsody.zip",
        ])
        assert cat.index_is_stale() is False
```

Add a `_build_tiny_catalog` helper near the top of the test file if one does not already exist (mirror the existing catalog-build test setup — look at `TestExternalCatalog` for the pattern of writing a file list and calling `build_from_file_list`).

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py::TestCatalogMeta -v`
Expected: FAIL (no `normalizer_version`/`index_is_stale`)

- [ ] **Step 3: Implement metadata**

In `catalog.py` `init_schema`, add a key/value meta table to the `executescript`:

```sql
            CREATE TABLE IF NOT EXISTS catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
```

Import the version: `from text_normalize import ... , NORMALIZER_VERSION`. In `build_from_file_list`, after the final commit, stamp it:

```python
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('normalizer_version', ?)",
            (str(NORMALIZER_VERSION),),
        )
        conn.commit()
```

Add methods:

```python
    def normalizer_version(self):
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM catalog_meta WHERE key='normalizer_version'"
            ).fetchone()
            return int(row[0]) if row else None
        except sqlite3.Error:
            return None

    def index_is_stale(self):
        """True when the FTS index was built with a different normalizer."""
        return self.normalizer_version() != NORMALIZER_VERSION
```

- [ ] **Step 4: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py::TestCatalogMeta -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/catalog.py kj-controller/tests/unit/test_catalog.py
git commit -m "feat(catalog): stamp + check NORMALIZER_VERSION on the index"
```

### Task 10: `rebuild_fts()` + trigram candidate index + reindex CLI

**Files:**
- Modify: `kj-controller/catalog.py`
- Create: `kj-controller/scripts/reindex_catalog.py`
- Test: `kj-controller/tests/unit/test_catalog.py`

- [ ] **Step 1: Write failing tests** (append)

```python
class TestRebuildFts:
    def test_rebuild_refreshes_tokens_and_version(self, tmp_path):
        from text_normalize import NORMALIZER_VERSION
        cat = _build_tiny_catalog(tmp_path, [
            "/m/KK-1 - Simon & Garfunkel - Sound Of Silence.zip",
        ])
        # Simulate a stale stamp, then rebuild.
        conn = cat._get_conn()
        conn.execute("UPDATE catalog_meta SET value='0' WHERE key='normalizer_version'")
        conn.commit()
        assert cat.index_is_stale() is True
        cat.rebuild_fts()
        assert cat.index_is_stale() is False
        # "and" query now finds the "&" row
        assert any("Sound Of Silence" in r["title"] for r in cat.search("sound of silence simon and garfunkel"))

    def test_trigram_candidates_returns_near_match(self, tmp_path):
        cat = _build_tiny_catalog(tmp_path, [
            "/m/KK-1 - Simon & Garfunkel - Sound Of Silence.zip",
        ])
        cands = cat._trigram_candidates("garfunkle", limit=20)  # typo
        assert any("Garfunkel" in r["artist"] for r in cands)
```

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py::TestRebuildFts -v`
Expected: FAIL

- [ ] **Step 3: Implement trigram table + rebuild**

In `init_schema`'s `executescript`, add a trigram FTS index for fuzzy candidate retrieval (separate from the main `media_fts`):

```sql
            CREATE VIRTUAL TABLE IF NOT EXISTS media_trigram USING fts5(
                norm_text,
                content='', tokenize='trigram'
            );
```

(`content=''` = a contentless/external-content-free index we populate manually with normalized text + rowid = media.id.)

Add the rebuild method to `ExternalCatalog`:

```python
    def rebuild_fts(self, callback=None, batch_size=5000):
        """Rebuild media_fts + media_trigram from the media table using the
        current normalizer, then restamp NORMALIZER_VERSION."""
        conn = self._get_conn()
        conn.execute("DELETE FROM media_fts")
        conn.execute("DELETE FROM media_trigram")
        conn.commit()
        rows = conn.execute("SELECT id, artist, title, disc_id FROM media").fetchall()
        total = len(rows)
        for start in range(0, total, batch_size):
            chunk = rows[start:start + batch_size]
            conn.executemany(
                "INSERT INTO media_fts(rowid, artist, title, disc_id) VALUES (?,?,?,?)",
                [(r[0], _normalize_for_search(r[1] or ''),
                  _normalize_for_search(r[2] or ''),
                  _normalize_for_search(r[3] or '')) for r in chunk],
            )
            conn.executemany(
                "INSERT INTO media_trigram(rowid, norm_text) VALUES (?,?)",
                [(r[0], _normalize_for_search(((r[1] or '') + ' ' + (r[2] or '')).strip()))
                 for r in chunk],
            )
            conn.commit()
            if callback:
                callback(min(start + batch_size, total), total)
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('normalizer_version', ?)",
            (str(NORMALIZER_VERSION),),
        )
        conn.commit()
        return total

    def _trigram_candidates(self, query, limit=200):
        """Return candidate rows via the trigram index (substring/fuzzy-friendly)."""
        norm = _normalize_for_search(query)
        if len(norm) < 3:
            return []
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT m.path, m.filename, m.folder, m.disc_id, m.artist, m.title, m.format "
                "FROM media_trigram t JOIN media m ON t.rowid = m.id "
                "WHERE t.norm_text MATCH ? LIMIT ?",
                ('"' + norm + '"', limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []
```

Also update `_flush_batch` (the initial build path) to populate `media_trigram` too, mirroring the rebuild inserts, so a fresh `build_from_file_list` is fuzzy-ready.

- [ ] **Step 4: Create the reindex CLI**

Create `kj-controller/scripts/reindex_catalog.py`:

```python
#!/usr/bin/env python3
"""Rebuild the catalog FTS + trigram indexes with the current normalizer.

Usage: python scripts/reindex_catalog.py [/path/to/external_media.db]
Run after deploying a normalizer change (NORMALIZER_VERSION bump).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config           # noqa: E402
from catalog import ExternalCatalog      # noqa: E402


def main():
    cfg = load_config()
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    cat = ExternalCatalog(cfg, db_path=db_path)
    if not cat.is_available():
        print("Catalog DB not found or empty; nothing to reindex.")
        return 1

    def progress(done, total):
        if done % 50000 == 0 or done == total:
            print(f"  reindexed {done}/{total}")

    print("Rebuilding FTS + trigram indexes...")
    total = cat.rebuild_fts(callback=progress)
    print(f"Done. {total} rows reindexed; stale={cat.index_is_stale()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(Confirm the `config` import name — if `config.py` exposes a different loader, match it; check how `app.py` loads config.)

- [ ] **Step 5: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py::TestRebuildFts -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add kj-controller/catalog.py kj-controller/scripts/reindex_catalog.py kj-controller/tests/unit/test_catalog.py
git commit -m "feat(catalog): rebuild_fts + trigram candidate index + reindex CLI"
```

---

## Phase 4 — Fuzzy fallback

### Task 11: rapidfuzz fallback in `catalog.search`

**Files:**
- Modify: `kj-controller/catalog.py`
- Test: `kj-controller/tests/unit/test_catalog.py`

- [ ] **Step 1: Write failing tests** (append)

```python
class TestFuzzySearch:
    def test_typo_in_artist_found(self, tmp_path):
        cat = _build_tiny_catalog(tmp_path, [
            "/m/KK-1 - Simon & Garfunkel - Sound Of Silence.zip",
            "/m/KK-2 - Queen - Bohemian Rhapsody.zip",
        ])
        results = cat.search("bohemian rapsody")  # missing 'h'
        assert any("Bohemian Rhapsody" in r["title"] for r in results)

    def test_exact_still_wins_without_fuzzy(self, tmp_path):
        cat = _build_tiny_catalog(tmp_path, [
            "/m/KK-2 - Queen - Bohemian Rhapsody.zip",
        ])
        results = cat.search("bohemian rhapsody")
        assert results and results[0]["title"] == "Bohemian Rhapsody"

    def test_garbage_query_returns_nothing(self, tmp_path):
        cat = _build_tiny_catalog(tmp_path, [
            "/m/KK-2 - Queen - Bohemian Rhapsody.zip",
        ])
        assert cat.search("zzzzxqwer plooof") == []
```

- [ ] **Step 2: Run, expect failure**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py::TestFuzzySearch -v`
Expected: FAIL (typo query returns nothing today)

- [ ] **Step 3: Implement fuzzy fallback**

At the top of `catalog.py`: `from rapidfuzz import fuzz`. Add a module constant `FUZZY_SCORE_CUTOFF = 80`. In `search()`, after the existing FTS block and BEFORE the `return self._like_fallback(...)` line, restructure so fuzzy runs only when nothing better was found:

```python
        # FTS5 primary (existing code) ...
        # (if rows: return [...])
        # LIKE fallback (existing)
        like_rows = self._like_fallback(normalized, limit, offset)
        if like_rows:
            return like_rows
        # Fuzzy fallback: rank trigram candidates by rapidfuzz score.
        return self._fuzzy_search(query, limit)
```

Add the method:

```python
    def _fuzzy_search(self, query, limit):
        norm_q = _normalize_for_search(query)
        if len(norm_q) < 3:
            return []
        candidates = self._trigram_candidates(query, limit=max(200, limit * 20))
        scored = []
        for c in candidates:
            hay = _normalize_for_search(((c.get("artist") or "") + " " + (c.get("title") or "")).strip())
            score = fuzz.WRatio(norm_q, hay)
            if score >= FUZZY_SCORE_CUTOFF:
                scored.append((score, c))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [c for _, c in scored[:limit]]
```

> `FUZZY_SCORE_CUTOFF` is the tuning knob; Task 14's metrics harness validates/refines it. Document it as such in a comment.

- [ ] **Step 4: Run, expect pass**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py::TestFuzzySearch -v`
Expected: PASS

- [ ] **Step 5: Run the full catalog suite**

Run: `cd kj-controller && pytest tests/unit/test_catalog.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add kj-controller/catalog.py kj-controller/tests/unit/test_catalog.py
git commit -m "feat(catalog): rapidfuzz fuzzy fallback over trigram candidates"
```

---

## Phase 5 — Route-level DRY

### Task 12: Unify `routes.py` normalization

**Files:**
- Modify: `kj-controller/routes.py`
- Test: `kj-controller/tests/integration/` (use existing search/route tests; add a focused one)

- [ ] **Step 1: Write a failing integration test**

Create `kj-controller/tests/integration/test_unified_search_normalization.py`:

```python
"""unified_search must find a '&' catalog row from an 'and' query (the bug)."""
import os
import pytest


@pytest.fixture
def app_with_catalog(tmp_path, monkeypatch):
    # Reuse the app factory + a tiny catalog. Mirror the setup used by other
    # integration tests (see tests/integration/ for the app fixture pattern).
    from app import create_app
    from catalog import ExternalCatalog
    db = tmp_path / "external_media.db"
    listing = tmp_path / "list.txt"
    listing.write_text("/m/KK-1 - Simon & Garfunkel - Sound Of Silence.zip\n")
    cat = ExternalCatalog({}, db_path=str(db))
    cat.init_schema()
    cat.build_from_file_list(str(listing))
    app = create_app()
    app.catalog = cat
    app.config["TESTING"] = True
    return app


def test_and_query_finds_ampersand_row(app_with_catalog):
    from routes import unified_search
    with app_with_catalog.app_context():
        result = unified_search(
            "Sound Of Silence - Simon and Garfunkel",
            app_with_catalog, grouped=False,
        )
    titles = [r.get("title") for r in result["local"]]
    assert "Sound Of Silence" in titles
```

> Adjust the app-fixture wiring to match how other integration tests build the app (check `tests/integration/conftest.py` / existing fixtures — `media` index may need stubbing). The ASSERTION is the contract; the fixture details follow existing patterns.

- [ ] **Step 2: Run, expect failure or fixture errors; iterate the fixture until the test runs and FAILS on the assertion**

Run: `cd kj-controller && pytest tests/integration/test_unified_search_normalization.py -v`
Expected: FAIL on the assertion (or first reveals fixture gaps to fix)

- [ ] **Step 3: DRY the route normalization**

In `routes.py`:

1. Replace `_normalize_song_key` entirely with a delegation:

```python
from text_normalize import group_key as _group_key

def _normalize_song_key(artist, title):
    return _group_key(artist, title)
```

(Or replace all call sites of `_normalize_song_key` with `_group_key` and delete the old function + its `_FEAT_RE/_PAREN_RE/_APOS_RE/_PUNCT_RE/_WS_RE` regexes if they are now unused — grep first.)

2. In `unified_search`, replace the naive local-media term matching (the block building `query_lower`/`query_terms`/`query_terms_clean` via `_strip_punct`) with the shared canonicalizer. Add these imports near the top of `routes.py`:

```python
from text_normalize import normalize as _normalize_text, tokens as _tokens
```

Then replace the matching block. The needles are canonical query tokens; the haystack is the canonicalized item text (both go through the same pipeline, so `and`/`&`, numbers, diacritics all align):

```python
    query_terms_clean = _tokens(query)
    for path, entry in app.media.index.items():
        if path in local_paths:
            continue
        searchable = _normalize_text(entry.get("display_name") or entry.get("filename", ""))
        if query_terms_clean and all(term in searchable for term in query_terms_clean):
            from catalog import parse_karaoke_filename
            disc_id, artist, title = parse_karaoke_filename(entry.get("filename", ""))
            local_results.append({
                "path": path,
                "filename": entry.get("filename"),
                "artist": artist,
                "title": title or entry.get("display_name", ""),
                "disc_id": disc_id,
                "format": os.path.splitext(entry.get("filename", ""))[1].lstrip('.'),
                "duration": entry.get("duration"),
            })
```

Delete the now-unused `query_lower`, `query_terms`, `_strip_punct`, and the `import re as _re` lines from that block.

- [ ] **Step 4: Run, expect pass**

Run: `cd kj-controller && pytest tests/integration/test_unified_search_normalization.py -v`
Expected: PASS

- [ ] **Step 5: Tidy external-engine queries (light)**

For KaraokeNerds / Divebar / YouTube calls, the query is sent to engines we don't control. Leave the raw `query` as-is for those (their engines handle their own matching) — do NOT over-normalize (e.g. don't strip `&` before sending to KN, which expects natural text). Add a one-line comment at each call documenting that this is intentional. (No behavior change; this is just to make the decision explicit and DRY-by-omission.)

- [ ] **Step 6: Run the route/integration suites**

Run: `cd kj-controller && pytest tests/integration -v`
Expected: PASS (fix any test that asserted the OLD grouping behavior of stripping brackets — per D4 brackets are now kept; update those expectations)

- [ ] **Step 7: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_unified_search_normalization.py
git commit -m "refactor(routes): route grouping + local-media search through shared normalizer"
```

---

## Phase 6 — Corpus, e2e recall, metrics, real samples

### Task 13: Deterministic messy-variant corpus generator

**Files:**
- Create: `kj-controller/tests/search_corpus.py`
- Create: `kj-controller/tests/fixtures/real_rotation_samples.json` (seed `[]`)
- Test: `kj-controller/tests/integration/test_search_corpus.py`

- [ ] **Step 1: Create the corpus generator**

Create `kj-controller/tests/search_corpus.py`:

```python
"""Deterministic messy-query corpus for search testing.

generate_variants(artist, title) yields (query, note) pairs that SHOULD all
retrieve the (artist, title) row. Deterministic: no randomness.
load_real_samples() returns committed real-world rows (hybrid layer, D1).
"""
import json
import os

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "real_rotation_samples.json")

_WORD_FOR_DIGIT = {"0": "zero", "1": "one", "2": "two", "3": "three",
                   "4": "four", "5": "five", "6": "six", "7": "seven",
                   "8": "eight", "9": "nine"}


def _digits_to_words(s):
    return " ".join(_WORD_FOR_DIGIT.get(ch, ch) for ch in s) if s.isdigit() else s


def generate_variants(artist, title):
    a, t = artist.strip(), title.strip()
    base = f"{t} - {a}"
    out = [(base, "base")]
    if "&" in a:
        out.append((base.replace("&", "and"), "amp->and"))
    if " and " in a.lower():
        out.append((base.lower().replace(" and ", " & "), "and->amp"))
    if "'" in base or "’" in base:
        out.append((base.replace("'", "").replace("’", ""), "drop-apostrophe"))
    # digit<->word for any standalone digit token
    toks = base.split()
    if any(tok.isdigit() for tok in toks):
        out.append((" ".join(_digits_to_words(tok) for tok in toks), "digit->word"))
    # diacritic stripped (ascii-ish) variant
    import unicodedata
    ascii_base = "".join(
        c for c in unicodedata.normalize("NFD", base)
        if unicodedata.category(c) != "Mn"
    )
    if ascii_base != base:
        out.append((ascii_base, "strip-diacritics"))
    # artist-only and title-only
    out.append((t, "title-only"))
    # single-typo (drop 4th char of the longest token >=6 chars) — deterministic
    long_toks = sorted([w for w in toks if len(w) >= 6], key=len, reverse=True)
    if long_toks:
        w = long_toks[0]
        typo = w[:3] + w[4:]
        out.append((base.replace(w, typo, 1), "typo"))
    # de-dup preserving order
    seen, dedup = set(), []
    for q, note in out:
        if q and q not in seen:
            seen.add(q)
            dedup.append((q, note))
    return dedup


def load_real_samples():
    """Return [{'query':..., 'expect_artist':..., 'expect_title':...}, ...]."""
    try:
        with open(_FIX, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
```

Create `kj-controller/tests/fixtures/real_rotation_samples.json` with exactly:

```json
[]
```

- [ ] **Step 2: Write the e2e recall test**

Create `kj-controller/tests/integration/test_search_corpus.py`:

```python
"""End-to-end: every deterministic messy variant must retrieve its source row."""
import pytest
from catalog import ExternalCatalog
from tests.search_corpus import generate_variants, load_real_samples

# A handful of real catalog-style rows (artist, title). Extend from fixtures.py.
ROWS = [
    ("Simon & Garfunkel", "Sound Of Silence"),
    ("Queen", "Bohemian Rhapsody"),
    ("Beyoncé", "Halo"),
    ("Twenty One Pilots", "Stressed Out"),
    ("AC/DC", "T.N.T."),
    ("Florence + The Machine", "Dog Days Are Over"),
]


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    d = tmp_path_factory.mktemp("corpus")
    listing = d / "list.txt"
    lines = [f"/m/ID{i} - {a} - {t}.zip" for i, (a, t) in enumerate(ROWS)]
    listing.write_text("\n".join(lines) + "\n")
    cat = ExternalCatalog({}, db_path=str(d / "external_media.db"))
    cat.init_schema()
    cat.build_from_file_list(str(listing))
    return cat


def _hits(cat, query, k=10):
    return cat.search(query, limit=k)


@pytest.mark.parametrize("artist,title", ROWS)
def test_all_variants_recall(catalog, artist, title):
    failures = []
    for query, note in generate_variants(artist, title):
        results = _hits(catalog, query)
        if not any(r["title"] == title and r["artist"] == artist for r in results):
            failures.append((query, note))
    assert not failures, f"missed variants for {artist} - {title}: {failures}"


def test_real_samples_if_present(catalog):
    samples = load_real_samples()
    if not samples:
        pytest.skip("no real samples committed yet")
    misses = []
    for s in samples:
        results = _hits(catalog, s["query"])
        if not any(r["title"] == s["expect_title"] and r["artist"] == s["expect_artist"]
                   for r in results):
            misses.append(s["query"])
    # Allow a documented miss budget; tighten as corpus grows.
    assert len(misses) <= len(samples) * 0.1, f"too many real-sample misses: {misses}"
```

- [ ] **Step 3: Run, expect pass (or surface real recall gaps)**

Run: `cd kj-controller && pytest tests/integration/test_search_corpus.py -v`
Expected: PASS. If a variant misses (e.g. an `AC/DC` punctuation case), that is a real finding — fix the normalizer or document the limitation in the spec's §6, then re-run.

- [ ] **Step 4: Commit**

```bash
git add kj-controller/tests/search_corpus.py kj-controller/tests/fixtures/real_rotation_samples.json kj-controller/tests/integration/test_search_corpus.py
git commit -m "test(search): deterministic messy-variant corpus + e2e recall suite"
```

### Task 14: Metrics harness (recall@K / precision)

**Files:**
- Create: `kj-controller/scripts/search_metrics.py`
- Test: manual run (script) + a thin smoke test

- [ ] **Step 1: Create the harness**

Create `kj-controller/scripts/search_metrics.py`:

```python
#!/usr/bin/env python3
"""Report recall@K and per-variant-type breakdown for the search corpus.

Usage:
  python scripts/search_metrics.py            # synthetic corpus over sample rows
  python scripts/search_metrics.py /path/to/external_media.db   # real catalog

Prints recall@1/@5/@10 overall and grouped by variant note, plus the count of
fuzzy-only saves. Use to tune catalog.FUZZY_SCORE_CUTOFF.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import ExternalCatalog                      # noqa: E402
from tests.search_corpus import generate_variants        # noqa: E402

SAMPLE_ROWS = [
    ("Simon & Garfunkel", "Sound Of Silence"),
    ("Queen", "Bohemian Rhapsody"),
    ("Beyoncé", "Halo"),
    ("Twenty One Pilots", "Stressed Out"),
    ("Florence + The Machine", "Dog Days Are Over"),
]


def _build(tmpdir):
    listing = os.path.join(tmpdir, "list.txt")
    with open(listing, "w", encoding="utf-8") as fh:
        for i, (a, t) in enumerate(SAMPLE_ROWS):
            fh.write(f"/m/ID{i} - {a} - {t}.zip\n")
    cat = ExternalCatalog({}, db_path=os.path.join(tmpdir, "external_media.db"))
    cat.init_schema()
    cat.build_from_file_list(listing)
    return cat


def _rank(results, artist, title):
    for idx, r in enumerate(results):
        if r.get("artist") == artist and r.get("title") == title:
            return idx + 1
    return None


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cat = _build(d)
        by_note = defaultdict(lambda: [0, 0])  # note -> [hits@10, total]
        ranks = []
        for artist, title in SAMPLE_ROWS:
            for query, note in generate_variants(artist, title):
                results = cat.search(query, limit=10)
                rank = _rank(results, artist, title)
                ranks.append(rank)
                by_note[note][1] += 1
                if rank is not None and rank <= 10:
                    by_note[note][0] += 1

        def recall_at(k):
            ok = sum(1 for r in ranks if r is not None and r <= k)
            return ok / len(ranks) if ranks else 0.0

        print(f"queries: {len(ranks)}")
        print(f"recall@1:  {recall_at(1):.3f}")
        print(f"recall@5:  {recall_at(5):.3f}")
        print(f"recall@10: {recall_at(10):.3f}")
        print("by variant type (hits@10 / total):")
        for note, (hit, tot) in sorted(by_note.items()):
            print(f"  {note:18s} {hit}/{tot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the harness**

Run: `cd kj-controller && python scripts/search_metrics.py`
Expected: prints recall@K lines; recall@10 should be high (≥0.95). If a variant type is weak, tune `FUZZY_SCORE_CUTOFF` or the normalizer and re-run.

- [ ] **Step 3: Add a smoke test**

Append to `tests/integration/test_search_corpus.py`:

```python
def test_metrics_harness_runs():
    import subprocess, sys, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.check_output(
        [sys.executable, os.path.join(root, "scripts", "search_metrics.py")],
        text=True, cwd=root,
    )
    assert "recall@10" in out
```

- [ ] **Step 4: Run it**

Run: `cd kj-controller && pytest tests/integration/test_search_corpus.py::test_metrics_harness_runs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/scripts/search_metrics.py kj-controller/tests/integration/test_search_corpus.py
git commit -m "test(search): recall@K / precision metrics harness + smoke test"
```

### Task 15: Pull real rotation samples (hybrid layer, D1)

**Files:**
- Modify: `kj-controller/tests/fixtures/real_rotation_samples.json`

- [ ] **Step 1: Locate a real-data source**

The device `rotation.db` is empty; the durable backup is the Google Sheet (`rotation_sync.py`). Determine reachability:

Run: `ssh nomadpctunnel 'cat /opt/nomad/kjbox/kj-controller/config.json 2>/dev/null | python3 -c "import sys,json; c=json.load(sys.stdin); print(c.get(\"rotation_sheet_id\"), bool(c.get(\"google_credentials_file\")))"' 2>&1 | head`
Expected: a sheet id + `True`, or empty. **This is read-only.**

- [ ] **Step 2: If reachable, export rows (read-only)**

If a sheet id + creds exist on the device, run a read-only export ON the device (do not modify device state):

Run (read-only; prints JSON to stdout, writes nothing on device):
```
ssh nomadpctunnel 'cd /opt/nomad/kjbox/kj-controller && python3 - <<PY
import json, gspread
from google.oauth2.service_account import Credentials
import config
cfg = config.load_config()
creds = Credentials.from_service_account_file(cfg["google_credentials_file"], scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["rotation_sheet_id"]).sheet1
rows = sh.get_all_records()
# Emit raw singer-entered song strings only.
print(json.dumps([r for r in rows], ensure_ascii=False))
PY'
```
Capture the output locally. Hand-label a sample of ~50-200 entries into the fixture shape `{"query":"<raw song_artist>", "expect_artist":"...", "expect_title":"..."}` by matching each against the catalog (use `catalog.search` to find the intended row; record the canonical artist/title). Only include entries where the intended match is unambiguous.

- [ ] **Step 3: If NOT reachable, document and proceed**

If no sheet/creds are reachable, leave `real_rotation_samples.json` as `[]` and add a note to the spec's §5.3 that the hybrid layer is pending a future export. The synthetic corpus (Task 13) still provides the recall guarantees. **Do not block on this.**

- [ ] **Step 4: Run the real-sample test**

Run: `cd kj-controller && pytest tests/integration/test_search_corpus.py::test_real_samples_if_present -v`
Expected: PASS (or SKIP if still empty)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/tests/fixtures/real_rotation_samples.json
git commit -m "test(search): add real rotation samples regression layer (hybrid)"
```

---

## Phase 7 — Docs + deploy

### Task 16: Document the module + reindex deploy step

**Files:**
- Modify: `kj-controller/docs/ARCHITECTURE.md`, `kj-controller/docs/CHANGELOG.md`

- [ ] **Step 1: Add an architecture section**

In `docs/ARCHITECTURE.md`, add a "Song-text normalization" subsection: the `text_normalize` module is the single source of truth (Python + JS twin), describe the pipeline order, the `NORMALIZER_VERSION` stamp, and that catalog FTS + every search call site route through it. Note the fuzzy fallback (rapidfuzz over the trigram index) and the metrics harness.

- [ ] **Step 2: Add a CHANGELOG entry**

Prepend a dated entry to `docs/CHANGELOG.md`:

```markdown
## 2026-06-04 — Unified song-text normalization + fuzzy matching
- New `text_normalize.py` (+ `static/text_normalize.js` twin) is the single source
  of truth for search normalization: &<->and, numbers<->words, diacritics, feat,
  abbreviations. Fixes the "Simon and Garfunkel" vs "Simon & Garfunkel" link bug.
- rapidfuzz fuzzy fallback for typos; new `media_trigram` index.
- **DEPLOY STEP (required):** after pulling, run
  `python scripts/reindex_catalog.py` on each device to rebuild the catalog index
  with the new normalizer (the service warns if `NORMALIZER_VERSION` is stale).
```

- [ ] **Step 3: Add a startup staleness warning**

In `app.py` (app factory, where the catalog is initialized), after catalog setup add:

```python
    try:
        if app.catalog.is_available() and app.catalog.index_is_stale():
            app.logger.warning(
                "Catalog FTS index is stale (normalizer changed). "
                "Run: python scripts/reindex_catalog.py"
            )
    except Exception:
        pass
```

(Match the actual attribute/order used in `app.py` for catalog init.)

- [ ] **Step 4: Full suite + commit**

Run: `cd kj-controller && pytest`
Expected: all PASS

```bash
git add kj-controller/docs/ARCHITECTURE.md kj-controller/docs/CHANGELOG.md kj-controller/app.py
git commit -m "docs+ops: document normalizer; warn on stale index; reindex deploy step"
```

---

## Final verification (before PR)

- [ ] `cd kj-controller && pytest` — all green
- [ ] `node --check kj-controller/static/app.js && node -e "require('./kj-controller/static/text_normalize.js')"` — JS valid
- [ ] `cd kj-controller && python scripts/search_metrics.py` — recall@10 ≥ 0.95 (record the number in the PR description)
- [ ] Manually reproduce the original bug fixed: a tiny catalog with "Simon & Garfunkel - Sound Of Silence" returns hits for query "Sound Of Silence - Simon and Garfunkel" (covered by `test_unified_search_normalization.py` and `test_search_corpus.py`)
- [ ] Reindex note present in CHANGELOG; staleness warning wired in `app.py`

---

## Notes on sequencing & risk

- Phases 1–2 are pure additions (no behavior change) → safe to land first.
- Phase 3 changes index-time normalization → the reindex (Task 10) and version stamp (Task 9) MUST ship together; production search is wrong until `reindex_catalog.py` runs on-device (frontend deploy auto-pulls but does not restart; the **backend** change requires a service restart — coordinate per CLAUDE.md production-safety rules).
- Per D4, grouping no longer strips brackets; Phase 5 Step 6 explicitly updates any test that encoded the old behavior.
- `FUZZY_SCORE_CUTOFF` is the single fuzzy knob; tune via Task 14's harness, not by editing matching logic.
