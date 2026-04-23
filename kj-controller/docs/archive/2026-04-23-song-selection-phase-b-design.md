# Phase B — Nerd view: per-row version expander

**Date:** 2026-04-23
**Parent:** [2026-04-23-song-selection-ux-master-plan.md](2026-04-23-song-selection-ux-master-plan.md)
**Depends on:** Phase A (grouped `/sing/search` response, `versions[]` snapshot)
**Blocks:** nothing

---

## Problem

From the master plan, the nerd's pain:

> "karaoke nerds" who really love karaoke, often pay attention

And what they need, in the KJ's words:

> as much info as needed for them to make the right choice, showing the karaoke production brands (where known) and formats, possibly release date metadata or filepath (collapsed/small font wrapped cos some of them are super long) or any other info which might help them choose. probably needs an explanation of the pros/cons of commercial (cover band backing track audio, traditional karaoke) vs community (original audio with ai removed).

Phase A landed the grouped response and shows a "N versions available →" affordance on each tile but leaves it inert. Phase B makes it clickable, reveals the per-version detail, and teaches a first-time nerd the commercial/community distinction without getting in the normie's way.

## Non-goals for this phase

- **Backfilling missing metadata** (release year, language, album). Phase B shows what the codebase actually has, labels gaps honestly where they matter, and doesn't fabricate. If we later decide year matters enough to scrape, that's a separate initiative — explicit in the master plan's non-goals.
- **Version-ranking / "recommended version" badges.** A nerd trusts their own judgement; ranking would be second-guessing them.
- **Per-persona opt-in ("I'm a nerd, show me everything by default").** Decision #2 in the master: progressive disclosure via per-row expander is the entire mechanism.
- **Enriching the KJ admin UI's picker.** The KJ-side picker from Phase A can optionally borrow the rendering helpers this phase introduces, but that's a tidy-up, not a blocker.

## Section 1 — What metadata we actually have

Grounded in an audit of the search pipeline (not wishful thinking). Fields per version kind:

### Local files
- `path` — full filesystem path
- `filename` — basename
- `artist`, `title` — parsed from filename
- `disc_id` — parsed from filename pattern (e.g. `SC1234` → `SC-1234`), when the pattern matches
- `format` — extension, e.g. `cdg`, `mp4`
- `duration` (seconds, optional) — from media index when available
- `folder_name` — the immediate containing folder (often conveys brand implicitly: `Sound Choice/`, `Chartbuster/`, etc.)
- `is_download` — True if it was downloaded via the app (vs. imported)
- `youtube_id`, `channel`, `upload_date` — populated only for app-downloaded YouTube rips

### Karaoke Nerds tracks
- `brand_name` — e.g. "Karaoke Version"
- `brand_code` — e.g. `KV`, `SK`, `OBSK`
- `youtube_url`
- `is_community` — True if the KN listing has the community checkmark (= user-uploaded, usually AI-vocal-removed)
- `in_library` — True if a same-artist-same-title local file exists (set in `unified_search`)
- `divebar` — optional sub-object (see below)

### Divebar (attached as `track.divebar` to a KN match)
- `file_id` — Google Drive ID
- `brand`, `brand_code` — same values, divebar is source of truth
- `format` — `mp4` or `cdg`
- `file_size` — bytes
- `drive_path` — path within the Google Drive catalog
- `quality` — e.g. `"HD"`, `"720p"`, or empty string
- `in_gcs` — whether mirrored to our GCS bucket (affects download path, not picker UX)

### Gaps we'll honestly surface as "—" or omit
- Release year (no source has it consistently)
- Album (no source)
- Language (no source)
- Vocal-removal algorithm for community tracks (no source; lives in brand reputation only)

The design below uses every field above where it carries signal and omits gracefully where it doesn't. Nerds prefer "partial truth, clearly labeled" to "pretend-complete metadata".

## Section 2 — UX design

### 2a. Collapsed state (unchanged from Phase A)

```
┌─────────────────────────────────────────────┐
│  Bohemian Rhapsody                          │
│  Queen                                      │
│                                             │
│  [ Let the KJ pick the best version → ]     │
│  6 versions available →                     │ ← now clickable
└─────────────────────────────────────────────┘
```

### 2b. Expanded state

Tap "N versions available →". The card grows inline (no modal, no navigation). The primary "Let the KJ pick" CTA stays at the top. Below it, a thin strip (one-time dismissible) explains the distinction the first time:

```
┌─────────────────────────────────────────────────────┐
│  Bohemian Rhapsody                                  │
│  Queen                                              │
│                                                     │
│  [ Let the KJ pick the best version → ]             │
│  Hide versions ↑                                    │
│                                                     │
│  ╭───────────────────────────────────────────────╮  │
│  │  ℹ️  Commercial vs Community                  │  │
│  │  Commercial — pro karaoke track, cover-band   │  │
│  │  audio, singalong classic.                    │  │
│  │  Community — original recording with AI       │  │
│  │  vocal removal, sounds like the real song.    │  │
│  │  [ Got it ]                                   │  │
│  ╰───────────────────────────────────────────────╯  │
│                                                     │
│  ┌─ In our library ────────────────────────────┐    │
│  │ 📁 Sound Choice — CDG                        │    │
│  │    Queen - Bohemian Rhapsody (SC1234).cdg    │    │
│  │    [ Pick this version → ]                   │    │
│  └──────────────────────────────────────────────┘    │
│  ┌─ Community karaoke ─────────────────────────┐    │
│  │ 🎤 Sound Choice — MP4 (HD)                   │    │
│  │    via Divebar · 56 MB                       │    │
│  │    /Drive/Sound Choice/1234 - Queen/…        │    │
│  │    [ Pick this version → ]                   │    │
│  └──────────────────────────────────────────────┘    │
│  ┌─ Online only (download needed) ────────────┐    │
│  │ 🌐 Karaoke Version                           │    │
│  │    Commercial · YouTube                      │    │
│  │    [ Pick this version → ]                   │    │
│  └──────────────────────────────────────────────┘    │
│  ┌─ Community (AI vocal removal) ─────────────┐    │
│  │ 🧑‍🤝‍🧑 ObsKure Community                     │    │
│  │    Community · YouTube                       │    │
│  │    [ Pick this version → ]                   │    │
│  └──────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

Rendering rules:

- **Primary CTA stays primary.** "Let the KJ pick" remains the big button. The expander is secondary: a nerd who wants to decide can; a nerd who opens it out of curiosity can still retreat to "KJ picks".
- **Sections are derived from the `versions[]` array**, not from source-type alone. The grouping into "In our library / Community karaoke / Online only / Community (AI removal)" is purely rendering:
  - *In our library* → `source: "local"`
  - *Community karaoke* → `source: "kn"` with a `divebar.file_id` (we can download it now from our mirrored library)
  - *Online only* → `source: "kn"` without divebar AND `is_community: false`
  - *Community (AI vocal removal)* → `source: "kn"` without divebar AND `is_community: true`
- **Empty sections are omitted.** If there are no KN-with-divebar results, no "Community karaoke" header appears.
- **Section order** is fixed (library → divebar → online-commercial → online-community). Reflects download immediacy (no download needed → download from our mirror → download from YouTube) but crucially also reflects "confidence the KJ would pick this" descending.

### 2c. Per-version row content

Each row surfaces the signal we have for its kind:

**Local (`source: "local"`):**
```
📁 <folder_name or brand_code if parseable> — <format, uppercased>
   <filename>
   <path if differs from folder_name prefix, small monospace, ellipsized>
   [ Pick this version → ]
```

**KN with divebar (`kn.divebar.file_id` present):**
```
🎤 <kn.brand_name> — <divebar.format.upper()><space>(<divebar.quality>)
   via Divebar · <human_size(divebar.file_size)>
   <divebar.drive_path, small monospace, ellipsized>
   [ Pick this version → ]
```

**KN commercial (no divebar, `is_community: false`):**
```
🌐 <kn.brand_name>
   Commercial · YouTube (download required)
   [ Pick this version → ]
```

**KN community (`is_community: true`, no divebar):**
```
🧑‍🤝‍🧑 <kn.brand_name>
   Community · YouTube (download required)
   [ Pick this version → ]
```

### 2d. Filepath/drive_path styling

The KJ explicitly asked for *"filepath (collapsed/small font wrapped cos some of them are super long)"*. So:

- CSS class `.version-path` — `font-family: ui-monospace, SFMono-Regular, monospace`, `font-size: 0.78rem`, `color: var(--nk-text-dim)`, `word-break: break-all`, `line-height: 1.25`, capped to 2 lines with `-webkit-line-clamp: 2` + `overflow: hidden`.
- If the path is shorter than the 2-line budget, it renders fully. If longer, truncates with `…`. Tapping expands (CSS-only, via a small `<details>` wrapper) for nerds who want to confirm the full path.

### 2e. Commercial vs Community explainer

Placement: **inline above the version list, the first time a singer expands any song's versions**. After they tap "Got it" once, it's hidden for the rest of the session (localStorage key `sing_rules_commercial_community_seen`).

Copy (final — tight, no jargon):

> **ℹ️ Commercial vs Community**
>
> - **Commercial** — a professional karaoke track: a cover band records the backing, lyrics are on screen, you sing the lead. Sounds like karaoke.
> - **Community** — the original recording, with the lead vocal removed by AI. Sounds like the real song.
>
> Most singers pick commercial for classics and community for recent or niche songs.

Dismissible with `[ Got it ]`. Never re-surfaced unless the singer clears their browser data.

## Section 3 — Client-side implementation

All changes in `static-sing/sing.js` and `static-sing/sing.css`. No backend changes this phase — the `versions[]` snapshot from Phase A already has everything needed.

### 3a. State additions

```js
state._expandedSongs = new Set();  // group keys currently expanded
const RULES_CC_SEEN_KEY = "sing_rules_commercial_community_seen";
```

### 3b. New render helpers

- `renderSongCard(group)` — replaces Phase A's tile renderer; checks `state._expandedSongs.has(group.key)` to decide collapsed vs expanded body.
- `renderVersionsExpander(group)` — renders the four sections in fixed order, filtering empties.
- `renderVersionRow(version, onPick)` — renders a single card row, dispatching on `version.source` and `version.kn?.divebar` / `is_community`.
- `renderCommercialCommunityExplainer(onDismiss)` — returns the strip, or `null` if already seen.

### 3c. "Pick this version" action

Essentially what Phase A's `pickLocal / pickKN` did, but fed from the grouped `versions[i]` object rather than a flat-list element. Helper:

```js
function pickSpecificVersion(version, group) {
  if (version.source === "local") {
    state.selected = {
      source_type: "local",
      source_ref: version.local.path,
      song_artist: version.local.artist || group.artist,
      song_title: version.local.title || group.title,
      label: `${group.title} — ${group.artist} (${version.local.filename})`,
    };
  } else if (version.kn?.divebar?.file_id) {
    state.selected = {
      source_type: "divebar",
      source_ref: version.kn.divebar.file_id,
      song_artist: group.artist,
      song_title: group.title,
      label: `${group.title} — ${group.artist} (${version.kn.brand_name})`,
      source_meta: {
        brand_code: version.kn.brand_code,
        disc_id: version.kn.divebar.drive_path,
      },
    };
  } else {
    state.selected = {
      source_type: "kn",
      source_ref: version.kn.youtube_url,
      song_artist: group.artist,
      song_title: group.title,
      label: `${group.title} — ${group.artist} (${version.kn.brand_name})`,
      source_meta: { brand_code: version.kn.brand_code },
    };
  }
  state.step = "confirm"; render();
}
```

Intentional: the resulting request is **not** `kj_pick`. It's a direct submission with the chosen version baked in — the KJ's admin UI sees it as a normal request with a "Approve" button, not a picker. Consistent with today's behaviour for anyone who picked a specific track.

### 3d. CSS

New classes, all under `.sing-version-expander` for scope:

- `.sing-version-expander` — wrapper, `margin-top: 0.5rem`.
- `.sing-version-section` — grouped per origin (`In our library`, etc.), `margin: 0.75rem 0`.
- `.sing-version-section h4` — small uppercase, muted (matches `.results h3` look).
- `.sing-version-card` — each candidate row.
- `.sing-version-path` — as described in §2d.
- `.sing-cc-explainer` — the one-time strip, with `.sing-cc-explainer[hidden]` for dismissal.

Keep line-count modest; follow the existing sing.css patterns (no CSS modules, no nesting, plain flat selectors).

### 3e. `sing.js` line-count concern

sing.js is already ~860 LOC. The follow-up memory in the workspace flagged that it's "approaching split-point". This phase adds maybe 150 LOC. If total exceeds ~1000 LOC, **extract `versions.js`** as an ES module containing `renderVersionsExpander`, `renderVersionRow`, `pickSpecificVersion`, `renderCommercialCommunityExplainer`, and the 4 section-rendering helpers. Import from `sing.js`.

Decision at implementation time: if the split pushes the PR over 600 lines of JS diff, extract it. Otherwise leave inline.

## Section 4 — Error handling & edge cases

| Situation | Behaviour |
|---|---|
| Group with exactly 1 version | No "N versions available" link shown. Phase A already handles this — expander code path never runs. |
| Group with many versions of the same brand (duplicates) | Render each; dedup is a later concern. |
| KN track has neither divebar nor a valid youtube_url | Currently can't happen — `karaoke_nerds.py` only yields tracks with URLs — but render as "Online only (download needed)" with the URL missing would 500 at approval time. Add a defensive filter in Phase A's grouping: drop KN tracks with blank `youtube_url` and no `divebar.file_id`. |
| A version's brand name is empty/missing | Fall back to `brand_code` uppercased. If both blank, show "Unknown brand". |
| A local filepath is < 40 chars | Render fully inline, not inside the 2-line-clamped monospace block. Short paths should be friendly to read. |
| localStorage unavailable (private browsing) | Explainer reshows every visit. Known trade-off; no workaround needed. |

## Section 5 — Testing strategy

### Unit (JS — minimal; project convention is no Jest)

Keep following the project convention: JS doesn't have automated tests, the pre-commit hook validates syntax only. This phase's JS is pure rendering with no complex logic; manual runbook + Playwright walkthrough is the right verification depth.

### Integration tests (Python)

Nothing new — the backend is unchanged from Phase A. Optionally add a regression test asserting the `versions[]` shape in `/sing/search` still matches what Phase B's renderer expects (catches the defensive filter from §4 above).

`test_sing_routes_search.py` (extend):

- Add test for "KN track with blank youtube_url and no divebar is filtered out of `versions[]`".

### Manual verification runbook (`docs/TESTING.md`)

- [ ] Expand a song with local + divebar + online-commercial + community variants. All four sections render. Order is library → divebar → online commercial → community.
- [ ] Commercial-vs-community explainer appears first expand, does not appear on subsequent expands (same session, same browser).
- [ ] Tap "Got it", reload page — explainer stays hidden.
- [ ] Open another song's expander — no explainer.
- [ ] A long drive_path (> 80 chars) clamps to 2 lines. Tapping the row (or the path element) expands via `<details>` to show the full path.
- [ ] Tap "Pick this version" on a local → confirmation shows local label, submits with `source_type=local`.
- [ ] Tap "Pick this version" on a KN+divebar → submits with `source_type=divebar`.
- [ ] Tap "Pick this version" on a KN-only → submits with `source_type=kn`.
- [ ] Primary "Let the KJ pick" still works after interacting with the expander.
- [ ] Re-collapse works (toggle `state._expandedSongs`).

## Section 6 — Implementation plan

| # | Task | Files touched |
|---|---|---|
| 1 | Defensive filter in Phase A's grouping for KN tracks missing both YouTube URL and divebar | `routes.py`, `tests/integration/test_sing_routes_search.py` |
| 2 | Inline rendering: expander toggle + four section helpers + row helpers | `static-sing/sing.js`, `static-sing/sing.css` |
| 3 | Commercial vs Community explainer + localStorage dismiss | `static-sing/sing.js`, `static-sing/sing.css` |
| 4 | (optional) Extract `static-sing/versions.js` if line-count crosses threshold | `static-sing/versions.js` (new), `static-sing/sing.js` |
| 5 | Manual runbook + CHANGELOG + architecture note | `docs/TESTING.md`, `docs/CHANGELOG.md`, `docs/ARCHITECTURE.md` |
| 6 | Version bump | `pyproject.toml` → `0.26.0` (minor — visible UX change) |

## Section 7 — Success criteria for Phase B

1. A nerd searching "Bohemian Rhapsody" expands the card and sees every version categorized into up-to-4 sections with accurate metadata (brand, format, quality where known, filepath where relevant).
2. A first-time expanded nerd sees the Commercial vs Community explainer once per browser. They can dismiss it. It does not reappear.
3. A singer picks a specific version and the confirmation screen shows the brand + source info in the label.
4. Long filepaths render in monospace, clamp to 2 lines, and expand on tap.
5. A normie who never taps the expander has zero new UI surface area compared to Phase A.
6. All existing tests still pass; no backend regressions.
