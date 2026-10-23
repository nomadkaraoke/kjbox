# Singer make-it friction: the night of 2026-10-22

**Andrew's report (verbatim):** "a singer at my karaoke night last night (ssh nomadpctunnel), anya, tried to submit a make / karaoke-gen request through the singer UI, and i watched her and it was pretty painful. first, she thought she'd already submitted the request immediately after the first piece of user effort / "work" (but it was actually confirming the song but still saying she needs to search for audio, and had several more steps including typing out her email (no easy google/apple sign in button or existing email saved to her profile). once i guided her to put in her email and choose an audio selection she managed to submit it but it took way too much effort for someone inebriated at a karaoke event. also unfortunately even though she entered "Helluva Boss - I Will Be Okay" or something into the box, it matched the song "Helluva Boss - You Will Be Okay" so I had to guide her to select the right audio for "I Will Be Okay" in the results, and i corrected the title using gen admin UI."

## Sources

- NomadPC `~/kjdata/action-logs/2026-10-22.jsonl` (every singer request and response), plus `rotation.db`
  (`sing_requests`, `sing_gen_accounts`, `rotation_archive`). Times below are UTC; local time is EDT (UTC-4).
- A replay of Anya's session on the local stack. A fake gen (`2026-10-23-singer-make-it-friction/replay/fake_gen.py`)
  serves the exact match-judge verdicts and audio-search results she got, with the Helluva Boss and Catfish rows from
  prod `song_id.db` added. Screenshots are 412 px wide (her phone was Android Chrome).

## Anya's Helluva Boss make-it, step by step

| UTC | What happened | Evidence |
|---|---|---|
| 01:57:35 | She types `i will be okay helluva boss`. On-device identification is **correct and confident**: `Helluva Boss — I Will Be Okay`. The library has no match, so she sees "Can't find it in our catalogue". | `/sing/search/identify` |
| 01:57:45 | She taps **Generate it →**, which is below the fold, under the tab bar ([1](2026-10-23-singer-make-it-friction/1-search-result.png)). | `make_submit` event |
| 01:58:20 | She has typed her email: **35 s** ([2](2026-10-23-singer-make-it-friction/2-email.png)). | `/make/send-code` |
| 01:58:37 | She switches to her mail app and types the code: **17 s**. | `/make/verify-code` |
| 01:58:39 | gen's fast check: catalog `kind:none, needs_ai`. The audio search finds the right track: Spotify, `I Will Be Okay`, match 1.0. The pick card shows it, but **"Use this audio" is disabled** while the AI check runs ([3](2026-10-23-singer-make-it-friction/3-audio-before-ai.png)). | `/make/check` fast, `/make/search` |
| 01:58:58 | gen's AI check (Gemini) says `kind:content, confident` → **"You Will Be Okay"**. The wizard then swaps the title, and also swaps the recommended audio to the wrong song ([4](2026-10-23-singer-make-it-friction/4-audio-after-ai.png)). In the "other options" list, the correct audio now carries **"Wrong track?"** and an unrelated `Winter Aid – Okay` gets **"Title match"** ([5](2026-10-23-singer-make-it-friction/5-other-options.png)). | `/make/check` full |
| 01:59:39 | With Andrew's help she picks index 3 (the right Spotify track). The confirm screen still says "You Will Be Okay", and the job goes in as **You Will Be Okay** with *I Will Be Okay* audio. Andrew fixed the title in gen admin. | `/sing/submit` |

From typing the search to submitting took about **2 min**. It needed roughly **13 actions**: type the search, scroll, tap Generate,
type the email, tap Send, switch to the mail app, type the code, wait about 20 s, tap "See all 17 other options", find the right
row, tap Select, then tap "Yes — send it in". She also had to judge filenames and badges along the way.

### Her second make-it (Catfish and the Bottlemen — Anything, 02:09–02:10)

She was already signed in, so it was quick: 56 s from tapping Generate to submitting. Then:

- 02:10:17: the submit succeeds and job 0761bafd is created.
- 02:10:21: **an identical second submit** (same search session) gets `409 search_expired`. gen consumes a search session on
  create, so the repeat 404s and kjbox maps that to "search expired".
- 02:10:32 and 02:11:06: she **searches for the same song again twice**, which strongly suggests she saw the error and thought it
  had failed. I couldn't reproduce a double submit locally (the button stays disabled), so the trigger is unknown (an
  Android double tap, a re-render, or a retry). The server should treat it as idempotent either way.

## Every make-it attempt that night (7 singers)

| Singer (device) | Result | Where the friction was |
|---|---|---|
| Anya ×2 | ✅ both submitted | wrong title from the AI check; a duplicate submit showed an error |
| martina ×2 | ✅ both | sent the code twice, 42 s apart (code slow to arrive or not seen?) |
| Ashlee A | ✅ | her **saved gen session had expired** (`401 signin_required`), so she was bounced back to email + code |
| Lyle | ❌ **abandoned at audio pick** | expired session as well. He typed `…@gmail.comk`, resent, verified, got a *perfect* Periphery match (176 seeders) and never tapped it. Ten minutes later he requested a different song. |
| Celeste | ❌ **abandoned twice** | 1st try: quit at the email screen. 2nd try: verified, but identification picked **artist "Sean Ono Lennon"** for Lana Del Rey's *Tomorrow Never Came (feat. Sean Ono Lennon)*, so the audio search returned junk. She requested a library song instead. |
| one more device | ❌ quit at the email screen | |

Overall: 9 wizard starts and 5 jobs. **Both drop-offs after sign-in happened at the audio-pick screen.**

## What's wrong, stage by stage, and ideas

### 1. Search result → "make it"
- The **green ✓ "Tidied to Helluva Boss — I Will Be Okay"** plus a form already filled with her song reads like a confirmation.
  The button that actually does something is below the fold, under the tab bar. Andrew saw her think it was done at
  about this point (or on the next screen, which shows her song title big).
- The screen has two numbered options, a "Describe it" link and a 5-line paragraph.
- **Idea:** when identification is confident and nothing in the library matches, show a single card above the fold:
  "**I Will Be Okay — Helluva Boss** · we don't have it yet · [🎤 Make it for me — free tonight]". Artist/title edit and
  YouTube go behind small links.
- Gap: the empty-state Generate card logs no `make_offer_shown`/`make_offer_tap`, so the tap-rate report misses it.

### 2. Email + 6-digit code
- Typing took 35 s plus an app switch. One email had a typo (`.comk`). Codes got resent twice. Two of four returning
  singers had expired sessions and were sent back to step 1 with a "signed out" message.
- **Ideas, from biggest win down:**
  - **A. Make the email optional and ask for it *after*.** Create the job with the box's admin token, the same way the KJ
    "Gen" button already does (`kj_make.py`). Afterwards, ask "Want the finished video emailed to you?" and claim the job
    to that account if they answer. There's no cost argument against this: show credits are free tonight anyway.
  - B. Google / Apple sign-in buttons (would need gen OAuth support for kjbox).
  - C. Keep the remembered email and refresh expired sessions silently; never show "signed out" to someone who never
    signed out.

### 3. Audio pick (where people drop off)
- Asking a tipsy singer to judge FLAC filenames, seeders and "Wrong track?" badges is the wrong job for them.
- **Bug:** a confident on-device identification (exact `song_id.db` row) was overridden by gen's AI `content` correction.
  Both songs exist in the catalog, and the AI picked the wrong one.
- **Bug:** the correction arrives about 20 s after the results, then swaps the title *and* the recommended audio under the
  user's thumb. The "Wrong track?" / "Title match" badges then follow the wrong title.
- **Bug (identification):** the feat. artist was chosen as primary for *Tomorrow Never Came*, so the search had nothing
  usable to find.
- **Ideas:**
  - **D. Don't show singers an audio picker at all.** Tier 1/2 (perfect or recommended) → auto-select and go to confirm.
    Tier 3 / nothing found → submit anyway, and the job waits for the KJ to pick audio (or uses a YouTube fallback).
  - **E. Trust exact local identification.** If the singer's title exactly matches a `song_id.db` row (or what the singer
    picked), skip the AI judge, or show its suggestion only as "Did you mean…?", never as an auto-correction.
  - F. Never change the song or the pick card after the button is enabled.

### 4. Confirm + submit
- The confirm screen repeats the title. In Anya's case it showed the wrong title, with no hint that it didn't match the
  audio she picked.
- **Fix (certain, small):** make submit idempotent. When the same device re-submits the same `search_session_id`, return
  the existing request instead of `409 search_expired`.

## The target

Search → one tap "Make it for me" → done screen ("Being made · ready in ~30 min · want it emailed? [optional]").
That replaces about 13 actions, an app switch and roughly 2 minutes with **2 taps**.
