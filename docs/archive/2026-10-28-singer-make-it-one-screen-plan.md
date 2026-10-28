# Singer make-it: one screen, one-tap sign-in, audio picked automatically (plan)

Evidence and stage-by-stage analysis: [2026-10-23-singer-make-it-friction-analysis.md](2026-10-23-singer-make-it-friction-analysis.md).

## Andrew's decisions (2026-10-28, verbatim)

> 1: "If the result is a perfect or recommended match, select it automatically." yes, but "If not" should still require the user to select. We should optimise the for the common path and make it more likely that most users won't need to do that, without adding more for me (KJ) to do myself.
>
> 2: "Trust the on-device match" sounds reasonable yes
>
> 3: "Email becomes optional" - no, i'd like to reduce friction here and prevent most users needing to type anything, but without sacrificing not capturing a valid email. my thoughts are 1) enabling google and apple login, since one of the two is very likely to be low friction for mobile users with no typing required. 2) still allowing email free text as a fallback if the user doesn't want to use google/apple, but ensuring the email field gets email auto-complete / pre-fill suggestions from the users browser so most users don't have to manually type it.
>
> 4. One "Make it for me" card above the fold when we're confident we don't have the song - yes, but let me review the UX before we ship.
>
> 5. Small, certain fixes: all sounds good to me.
>
> we should review the UX and perhaps try to combine more of the submission flow into a single screen to make sure an inebriated/impatient user doesn't think they're done already when there's more to do

## Shape: three PRs

| PR | Repo | What | Ships |
|---|---|---|---|
| **A. Fixes** | kjbox | Section A below | straight away (no UX change to review) |
| **B. Partner social sign-in** | karaoke-gen | `POST /api/kjbox/auth/google` and `/apple` | ahead of C; inert until kjbox calls it |
| **C. One-screen make-it** | kjbox | make-it card + single make screen + sign-in buttons + auto audio | **after Andrew reviews screenshots of the real UI** |

## A. Fixes (kjbox)

1. **Trust the on-device match.** The make wizard gets told where its artist/title came from. When they match the
   singer's confident on-device identification (a real `song_id.db` row, or a candidate the singer tapped), skip gen's
   AI judge. Only the fast catalog check still runs, and only for cosmetic tidies. A confident AI `content` verdict
   that disagrees is shown as an opt-in "Did you mean X?" and never applied automatically. Typed or edited
   artist/title keep today's judge behaviour.
2. **Nothing changes under the singer's thumb.** Once the audio button is enabled, a late verdict can't swap the
   title or the recommended audio. It can only add a "Did you mean…?" line.
3. **Badges follow the song the singer is making.** "Wrong track?" / "Title match" are computed against the current
   title. With 1 and 2 in place, they can no longer point at an AI-swapped title.
4. **Idempotent make submit.** If the same device submits the same `search_session_id` again, return the existing
   request (200) instead of `409 search_expired`. This is done in `sing_make.create_job`'s caller, before gen is
   called again.
5. **Featured-artist identification.** For *Tomorrow Never Came (feat. Sean Ono Lennon)*, identification chose the
   featured artist as primary. Fix it in `song_identify` (or in the build's artist choice) and add the case to
   `tests/fixtures/song_id_eval.jsonl`.
6. **Keep the email on sign-in loss.** On `signin_required` (an expired gen session), keep the remembered email
   pre-filled and show "Welcome back, confirm it's you" instead of "you were signed out".
7. **Log the empty-state card.** Fire `make_offer_shown` / `make_offer_tap` with `where: empty_state`, so
   `search_log_report.py` counts it.

Tests: unit tests for 1/2/4/6 (pytest + the existing make.js test harness, if there is one; else node tests for the
pure parts), an eval row for 5, and Anya's replay re-run on the build host showing the title stays *I Will Be Okay*.

## B. gen: partner Google / Apple sign-in

- `POST /api/kjbox/auth/google` and `/api/kjbox/auth/apple` `{id_token, locale?, venue?}` → the same response as
  `/api/kjbox/auth/verify-code` (`session_token, user, credits_granted, credit_status`).
- Reuse `login_code._verify_identity` (audiences, the `fake:` tokens on the local stack) and kjbox's venue rules: no
  per-IP signup cap, the partner-wide 24h new-account cap, and the show credit grant. Apple relay addresses are
  allowed as their own account, like the app.
- **Session lifetime for partner sign-ins** (open question 2): today it's 7 days of inactivity, so a weekly regular is
  always signed out by the next show.
- **Andrew's one-off setup (GCP is read-only for me):**
  - Google: add `https://sing.nomadkaraoke.com` as an Authorized JavaScript origin on the existing "Nomad Karaoke Web"
    OAuth client (`718638054799-fush0…`, already an accepted audience).
  - Apple: create a **Services ID** for web (e.g. `com.nomadkaraoke.sing`), add the domain `sing.nomadkaraoke.com`
    and its return URL, and add the Services ID to gen's `APPLE_SIGNIN_AUDIENCES`. Domain verification, if Apple
    still asks for it: kjbox serves the `.well-known` file.

## C. One-screen make-it (kjbox) — for Andrew's UX review

### Search result, when we're confident we don't have it

```
┌──────────────────────────────────────┐
│ I Will Be Okay                       │
│ Helluva Boss                         │
│ We don't have a karaoke version yet. │
│ [ 🎤  Make it for me — free tonight ]│  ← above the fold
│ not this song? · paste a YouTube link│
└──────────────────────────────────────┘
```
No "✓ Tidied to" success tick on this path, so it can't read as "done". The artist/title fields and the long
paragraph move behind "not this song?".

### The make screen: everything on one page, one final button

```
  Step 2 of 2 · Not sent yet                  ← progress so "done" is unambiguous
  ┌ Making ─────────────────────────────┐
  │ I Will Be Okay — Helluva Boss  edit │
  │ 🎧 Finding the audio…  → ✓ Audio found: official soundtrack (change)
  └─────────────────────────────────────┘
  ┌ Where should we send your video? ───┐
  │ [ G  Continue with Google ]          │  ← order: Apple first on iOS
  │ [   Continue with Apple  ]          │
  │ or use your email                    │
  │ [ you@example.com ▾ autofill ]       │  <form>, name=email, autocomplete=email
  │   → [Email me a code] → code box     │  inline, same screen
  └─────────────────────────────────────┘
  Singing with anyone? (optional) · photo OK? (if asked)
  [ 🎤 Make it & add me to the rotation ]   ← the only "send"; disabled with a reason
                                              ("Sign in above first" / "Finding audio…")
```
- The confirm screen is folded in: partners and photo consent live here, and the big button submits. The "done"
  screen (✨ Being made for you) only appears after the request really went in.
- Already signed in (returning in the same week): the sign-in box shows as `Sending to anya@… · not you?`.
- **Audio:** the search starts as soon as sign-in completes. It runs as the singer's gen account because the credits
  and job ownership depend on it, and one-tap sign-in takes about 2 s. A tier 1/2 result is selected automatically
  (with a small "change" link). For tier 3 the box expands to "We're not sure which recording is right — pick one" with
  the list inline, and the button stays disabled until they pick. Nothing new is added to the KJ's work.
- **Making tier 3 rarer:** A1 (correct titles) and A5 (correct primary artists) remove the two failures we saw. Also
  search with the identified canonical artist/title rather than a featured-artist variant.

### Review gate

Build it for real and run it on the local stack **on the build host** (`ssh andrewmastech`), replaying Anya's
Helluva Boss and Catfish sessions plus a tier-3 case, at phone width. Send Andrew the screenshots and a short screen
recording. Merge only after he signs off.

## Open questions for Andrew

1. Sign-in button order: Apple first on iPhone and Google first everywhere else (my default), or always Google first?
2. Partner-session lifetime: should gen keep kjbox sign-ins alive longer (e.g. 30 days of inactivity) so weekly
   regulars stay signed in? With one-tap Google/Apple re-sign-in this matters less; it's mainly for the email path.
3. "Step 2 of 2 · Not sent yet" wording, or no step counter at all? (screenshots will help)
