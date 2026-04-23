# Song Selection UX — Master Plan

**Date:** 2026-04-23
**Scope:** Overhaul of the singer-facing "Pick your song" screen and the KJ approval flow that receives its output.
**Phasing:** Three phases, ship in order A → B → C. Each phase is independently shippable with no hidden dependencies, but Phase A must precede the others because it reshapes the search response contract.

---

## Problem

The current "Pick your song" screen shows every variant of every match in a flat list — local files, every KN brand, community-made versions, divebar mirrors — for every query. For most singers this is too much information; for some it's not enough of the right information; for a small but growing minority the page can't help them at all because their song isn't in the catalog.

The KJ's words on who actually walks up to the QR code each night:

> 1. "normies" who don't care to think about there being different versions of a song, they typically want to sing something common and don't care what version it is as long as it's fairly normal.
> 2. "karaoke nerds" who really love karaoke, often pay attention
> 3. "punks" who often want to sing something from niche local band

All three are failed by the current UI in different ways: normies are overwhelmed, nerds aren't given enough info to actually pick a good version, punks hit a dead end with no path forward.

## Personas (summary)

| Persona | What they want | What they see today | What goes wrong |
|---|---|---|---|
| **Normie** | Submit a common song, sing it, go back to their table. | 5–10 rows for "Bohemian Rhapsody" — Queen, each tagged with brand codes they don't recognise. | Decision fatigue; picks randomly or taps the first row. KJ sometimes ends up with a weak version even when a great one exists. |
| **Nerd** | Pick a *specific* version — right brand, right format, right backing-track style. | Brand code (`KV`, `SK`, `OBSK`) with no explanation of what those brands *are*, no year, no format info, no filepath, no commercial-vs-community distinction. | Guesses; sometimes regrets their choice when the song starts. |
| **Punk** | Sing something from a band with no commercial karaoke release. | Empty search results, or one mediocre community-made version. | Either abandons the flow or submits a bad version and hopes. |

## Decisions locked in

Confirmed with the KJ on 2026-04-23 before any code was written:

1. **Grouping logic (Phase A).** Normalize `(artist, title)` to lowercase, trim, strip `feat./ft.`, strip bracketed qualifiers, exact match. No fuzzy matching in v1 — accept that `"Don't Stop Believin'"` and `"Dont Stop Believin"` won't merge until someone complains.
2. **Progressive disclosure (Phase B).** Per-row inline expander — no global "show all versions" toggle, no settings page. Normies never see variants unless they tap "N versions available →"; nerds discover it within a visit.
3. **"Make it" availability (Phase C).** Single global switch in the KJ Requests-settings modal ("Accept make-it requests tonight") alongside the existing `auto_approve` toggle. When ON, singers see the option and can submit `source_type=make` requests; KJ can still reject individual ones. When OFF, the option disappears from the UI and the backend rejects `source_type=make` submissions.
4. **KJ picker UX (Phase A).** Inline expansion on the pending-request card — list of candidate versions with the same rich metadata the nerd view shows, each with its own "Approve with this version" button. No modal.
5. **Doc structure.** 1 master plan + 3 per-phase combined design+implementation docs. All four dated `2026-04-23`. Master captures persona framing + phase ordering + direct quotes.

## Phase summary

Full design and implementation plans in the three per-phase docs. Each doc is self-contained and can be executed without re-reading the master.

### Phase A — Simple-default search + KJ version picker

**Design + plan:** [2026-04-23-song-selection-phase-a-design.md](2026-04-23-song-selection-phase-a-design.md)

**Ships the normie path.** `/sing/search` groups results by normalized `(artist, title)` into one logical row per song. Singer UI shows one tile per song with "Let the KJ pick the best version" as the default CTA. Submission carries the full candidate set forward to the KJ admin UI. KJ approval modal grows an inline version-picker so "Let the KJ pick" requests can actually be approved with a specific file bound.

**Shipped when:** a normie searches "bohemian rhapsody", sees one tile, taps "Add to queue", and the KJ approves it by tapping one of the candidate versions — singer never sees brand codes; KJ never has to manually search the catalogue.

**Coupled with the picker** — these ship together or not at all (a submission that says "KJ picks" needs a UI that lets the KJ actually pick).

### Phase B — Nerd view: per-row version expander

**Design + plan:** [2026-04-23-song-selection-phase-b-design.md](2026-04-23-song-selection-phase-b-design.md)

**Ships the nerd path.** The grouped tile from Phase A gets an inline "N versions available →" affordance. Tapping expands a stack of per-version rows with the metadata we actually have — brand, format, filepath (collapsed + monospace + ellipsized), quality (when known), community-vs-commercial badge. One-time dismissible explainer strip defining "Commercial" (cover-band backing audio) vs "Community" (original recording, AI vocal removal) so a nerd who doesn't know the distinction can learn it in situ.

**Shipped when:** a nerd who wants the Sound Choice CDG version of "Livin' on a Prayer" can find it and pick it without asking the KJ, and they understand why they might prefer it over the community version.

### Phase C — Empty-state and the make-it flow for punks

**Design + plan:** [2026-04-23-song-selection-phase-c-design.md](2026-04-23-song-selection-phase-c-design.md)

**Ships the punk path.** Redesigned no-results state that explicitly walks the singer through three escape hatches, in order of expected success:

1. Paste a YouTube link (existing flow, better framed).
2. Ask the KJ to make one tonight — with the full caveats spelled out.
3. Make it yourself on gen.nomadkaraoke.com right now, on your phone, and paste the published YouTube link back in.

The KJ's words on the caveats for option 2:

> making a track live on the night isn't always available (as if the KJ is too busy to do lyrics reviews during a show, they need to be able to turn the feature off), and even when it is available, it isn't always possible for every song on the night (some songs need a long and high amount of focus to correct/sync lyrics during the generator lyrics review phase), and how long it takes will vary (sometimes 20 mins, sometimes 1+ hours).

And on option 3:

> if they're willing to do the lyrics review themselves they can just make the song same-night themselves on their own phone, then just input the youtube url into the song request form once it's published. that process can take as little as 5 minutes of them focusing on their phone to complete.

**Shipped when:** a punk who just searched "[obscure local band] - [obscure song]" and got no hits has a clear, unapologetic three-option menu that sets correct expectations for each route.

## Phase ordering rationale

- **A first** because it reshapes the search response contract — B and C both assume the grouped response shape and would need rework if A changed after them.
- **B before C** only because B is smaller and further-reaching (every nerd every visit) while C is narrow (only on no-results and only for a subset of singers). If capacity changes, B and C can swap.

## Cross-cutting implementation notes

Things worth noting once here rather than three times:

- **Plans go under `kj-controller/docs/archive/`**, not the worktree root. That's where sub-project #4 shipped and where the convention lives.
- **No DB migrations planned.** All three phases reuse `sing_requests` as-is. Phase A adds a new `source_type` value (`kj_pick`) + stores the candidate snapshot in the existing `source_meta` JSON column. Phase C adds one config key (`sing_accept_make_requests`) to `rotation_meta`.
- **No new dependencies.** Everything is vanilla JS + existing Python.
- **i18n does not apply** — kjbox-singer pages are English-only; the i18n policy in the workspace root CLAUDE.md is for gen/decide/website.
- **Production safety.** Each phase requires a `kj-controller` restart because of Python route changes. Ship each phase as its own PR; do not combine.

## Non-goals

Things that are tempting but explicitly out of scope:

- **Fuzzy matching** on song titles (decision #1). The normalization logic will catch 80% of dupes; the remaining 20% can be solved later with a known-false-negative list.
- **Version-quality heuristics** picking a "best" default version on the backend. The KJ picks manually for "KJ picks" requests; no ranking algorithm.
- **Backfilling missing metadata** (year, language, album). Phase B shows what we have. If we later decide year matters enough to scrape it, that's a separate initiative.
- **A singer-facing "my history"** or **"returning singer"** view. That's sub-project #2 on the existing roadmap (`2026-04-18-singer-identity-roadmap.md`) and unrelated.
- **Payment-for-priority** is a separate roadmap (`2026-04-18-payment-integrations-roadmap.md`); make-it pricing stays free in this initiative.
- **Automated "make it" capacity detection** — deciding per-song whether the KJ can handle it. Too much signal we don't have. The KJ toggle + per-request reject is the v1 answer.

## Success criteria

Shipping the whole initiative is complete when:

1. A normie who searches "bohemian rhapsody" sees one card and a primary CTA. They don't see the words "brand", "CDG", "MP4", "community", "commercial", "OBSK", "KV", or "Sound Choice" anywhere on their flow unless they tap the expander.
2. A nerd who wants the Sound Choice CDG can find it and pick it without asking the KJ.
3. A punk who searches for their niche song and gets zero hits has three labelled paths forward, with the "make it yourself" path including a 1-screen explainer of the 5-minute gen.nomadkaraoke.com flow.
4. The KJ can approve a "Let the KJ pick" request in ≤ 2 taps on the admin UI (open request → tap one version).
5. The KJ can toggle make-it acceptance at the start of a busy night and the singer UI reflects it on next page load.
6. No regressions on the existing flow: a nerd who knows what they want can still submit a specific version in one tap (via the expander).
