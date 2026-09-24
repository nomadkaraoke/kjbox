# Singer UI — full product walkthrough findings + i18n plan (2026-09-23)

**Spec (Andrew, verbatim):**

> yes please help me walk the product and use the live singer web ui https://sing.nomadkaraoke.com/?t=2121 as if we're a real singer (nudging things in the live kj ui https://kjbox.nomadkaraoke.com/ to simulate a real karaoke night too) and give each part of the singer user experience some proper thought, make sure everything works and we've made it easy to use and intuitive for very different people of all backgrounds, ages, genders, cultures etc. who walk into my karaoke night. we should also make it full localised with the auto translation pipeline etc. like we've done for karaoke-gen.

## How the walkthrough was run

- Live singer UI at 390×844 (iPhone-ish) in Playwright; two personas: **Maria G.** (no phone,
  desktop Chrome UA) and **José Álvarez** (iOS Safari UA, `es-ES` locale, phone number).
- KJ side driven through the real box API via the Cloudflare Access service token
  (`/rotation/status` batch updates to walk Now Singing → Done, `/rotation/requests/*/approve|reject`).
  No playback was started (audio is on the venue HDMI); statuses alone drive the singer UI.
- Box was idle (state `stopped`, only Andrew's two test entries) — safe to simulate.

## What works (verified on the live box)

Code entry (bad + good code) → name → search → 12-version expander (pills, brand modal, tech
modal, ▶ Preview plays the real file) → auto-select → confirm → auto-approve → rotation #3 →
"1 to go" → "You're next" → "You're up" → "Already sung" · second song (YouTube source →
box download queue fired) · ⇄ Change (supersede kept the slot after KJ approval) · ✕ Cancel ·
duet partner chips (partner canonicalised to "Maria G.") · iOS notification hint + SMS line ·
drag-to-reorder · tip claim → KJ confirm/reject → ♥ on entries + status lines · empty-state
triage · house rules.

## Findings — prioritised

### P0 — real-night risks / bugs

1. **Shared-IP rate limit will trip for a whole venue.** Every singer mutation (submit, cancel,
   change, reorder, update-phone, rename) shares ONE per-IP budget (`sing_rate_limit_per_ip`
   = 5 / 300 s). Cloudflare passes the phone's public IP — on venue wifi that is the venue's
   single NAT address. Two personas on one laptop hit it within 4 minutes: José's 3rd song,
   his reorder ("Couldn't save the new order — see the KJ", no hint it's a limit) and rename
   all 429'd. Fix: budget per **device** (device_id / edit-token owner) with a much higher
   per-IP ceiling; one honest "too many changes right now" message everywhere.
2. **Search buries the songs people actually want.** Groups keep local-catalog order (FTS
   rank ties → rowid) and the local catalog is capped at 10 rows before grouping:
   "despacito" → Luis Fonsi 5th behind Boyce Avenue; "hallelujah" → Jeff Buckley 8th,
   Leonard Cohen 10th, Bon Jovi/Standard/Rammstein first. Fix: score groups (exact-title
   match, in-library, version count as popularity, venue play count) and raise the local cap
   for the grouped singer flow.
3. **Single-version songs lose the whole decision layer** (Andrew's device finding): no
   Preview, no Community/Commercial pill, no brand/format — just "Add to queue".
4. **Change-song mode is invisible.** Search and confirm screens look identical to a fresh
   request; after submit the replacement appears as a *second* song ("Waiting for KJ to
   approve…") next to the original with nothing saying it replaces it.
5. **A song that is playing right now can still be Changed/Cancelled** from My songs.

### P1 — clarity for first-timers / non-native speakers

6. "KJ" is jargon (karaoke jockey) — used ~40 times, never explained. Singer copy now says
   "the host" (first mention "your host (the KJ)") — trivially revertible in `en.json`.
7. Idioms that don't translate/land for everyone: "break a leg", "nice one!", "flag the KJ
   down", "rock solid". Replaced with plain language.
8. Two different primary buttons for the same intent ("Add to queue" vs "Auto-select best
   version →"). Unified to **"Request this song →"** with a secondary "Best version picked
   automatically · N versions →" line.
9. Empty-state header says "Three ways forward" and numbers cards 1./3. when the KJ has
   make-requests off (card 2 hidden).
10. Duets show only the lead's first name in the rotation ("José" for "José Álvarez & Maria G.").
11. Rotation rows truncate song titles at 390 px ("Maxïmo Park – Bo…") — the two-line grid
    only kicks in ≤380 px. Next-song bar truncates both the song and the status.
12. "About 1 song to go" on My songs while the Rotation tab says "up next" for the same
    position (someone is on the mic). Status banner "#3 · ~6–12 min" reads as a code.
13. House rules reference a screen title that no longer exists ("'Looking good?' screen"),
    hard-code "$20+" instead of the configured tip threshold.
14. Name screen: no branding at all (a QR scan lands on "Request a song" with no venue/brand),
    Australian phone placeholder (+61) at a US venue, long legalistic SMS consent; search box
    isn't focused after "Next".
15. Local files with an unregistered disc-id prefix show the raw id as the *brand*
    ("EEK-01507", "VSM-00162", "Unknown brand").
16. `change number` link renders default blue inside the green "on" line; favicon 404 on the
    public host (no favicon in `static-sing/`); the notifications block pops in 2 s after the
    songs list (layout shift); two "keep this page open" lines on the same screen.

### Not changed (noted for Andrew)

- Tip tab pre-selects the $20 (threshold) preset — mirrors the live /tip page; could feel pushy.
- 300 ms anti-mis-tap window silently swallows a tap on freshly rendered results.
- Singer can't see that their YouTube-sourced track is still downloading on the box.
- KJ singer_stats mark a duet partner as `has_tipped` when only the lead's entry was hearted.

## i18n plan (mirrors karaoke-gen)

- `kj-controller/static-sing/messages/en.json` (nested keys, `{placeholder}` interpolation)
  + 33 locale files produced by `scripts/translate.py` (copied from gen, same Gemini/Vertex
  two-pass pipeline + shared GCS cache `nomadkaraoke-translation-cache`), `glossary.json`,
  `validate-translations.py`. Workspace `scripts/translate-all.sh` gains a 6th directory.
- Runtime `static-sing/i18n.js`: locale = `?lang=` → `localStorage sing_lang` →
  `navigator.languages` best match → `en`; loads `messages/<locale>.json` (en embedded via
  fetch too, one round-trip, SW-cached); `t(key, vars)`, plural via `Intl.PluralRules`
  (`key.one/other`), `dir=rtl` for ar/he, `<html lang>`; template strings via `data-i18n`.
- 🌐 language switcher pill on the identity screen and in the tab bar overflow (native names).
- CI: `.github/workflows/i18n.yml` key-parity check; `.githooks/pre-commit` auto-translates
  when `en.json` is staged (same as gen).
- Currency stays the KJ's ("$"); times use translated unit strings.
