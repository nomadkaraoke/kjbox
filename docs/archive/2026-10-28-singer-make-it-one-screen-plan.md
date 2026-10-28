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

## Andrew's answers (2026-10-28, verbatim)

> 1: yes that sounds reasonable whatever will be most intuitive for users. see mobbin MCP for UX inspiration / to try and model it based on other commonly used apps/best practices though.
> 2: we should change that to 30 days globally tbh, but agreed that one-tap sign in makes this low friction once we get apple/google working anyway
> 3: not sure what this means if we make it a single screen page but let's build first and I can decide later

So:
- **Button order and layout:** model them on common apps, using Mobbin (sign-in sheets, checkout one-pagers). Research
  this before building C.
- **gen sessions:** change `SESSION_EXPIRY_DAYS` (inactivity) from 7 to **30 days globally**, in PR B.
- **Step counter:** build C without one; Andrew decides from the screenshots.

## Mobbin research (2026-10-28) → PR C design

**1. Checkout one-pagers** ([talabat](https://mobbin.com/screens/4918cd52-367d-4da3-9ad9-e381ddeba895), [Deliveroo](https://mobbin.com/screens/6dec1d5e-be7a-49b3-9667-d4b775ad04b0), [Uber Eats](https://mobbin.com/screens/e9d30576-6c0d-4213-b4b1-689a38c6760e), [Subway](https://mobbin.com/screens/cacb0514-5d2f-4d3e-a425-a14439e11b20)).
One scrolling page of summary rows. Each row is **pre-filled with a sensible default** plus a small "Change" link. A **single full-width primary button is pinned to the bottom** and names the outcome ("Place order"). Nothing else on the page looks like a primary button.
→ The make screen copies this. Each row is filled in with a "Change" link:
- **Song:** "I Will Be Okay — Helluva Boss"
- **Audio:** "✓ Official soundtrack · Spotify", picked automatically
- **Send your video to:** the sign-in

A sticky **"🎤 Make it & add me to the list"** button is the only primary button. While something is missing it is disabled and says what ("Sign in above first", "Finding the audio…"). Our current wizard instead has a primary button on every step ("Email me a code", "Use this audio", "Yes — send it in"), and each one feels like the last.

**2. Sign-in blocks** ([Grubhub](https://mobbin.com/screens/42d69905-f312-4d5a-bc3d-dc7f308ed080), [monday.com](https://mobbin.com/screens/e8db9e52-9383-4a53-ab24-6b7ccedaa136), [Dropbox](https://mobbin.com/screens/bdf5e75d-87e2-46ae-bb1c-fb44d71d4841), [Monarch](https://mobbin.com/screens/7a7fa802-5ee6-4eff-95e6-50d508b02444), [Withings](https://mobbin.com/screens/ac8a2c06-98ae-4fec-9558-8ad3b1da79b7)).
Social buttons come first. They are full-width and stacked, each with the provider's logo and "Continue with …". Below an "or" divider sit one email field and "Continue". Cross-platform apps put Google first (Grubhub, monday, Dropbox); Apple-centric apps put Apple first (Monarch, Withings).
→ Apple first on iOS, Google first elsewhere. The email path stays inline on the same card: Continue sends the code and the code box appears in place. The email field sits in a real `<form>` with `autocomplete="email"` so browser autofill offers it.

**3. Order-placed screens** ([Panera](https://mobbin.com/screens/68fbc9be-b216-4d7f-a7d9-2b0cd7cf7c76), [Wonder](https://mobbin.com/screens/60bb3c66-d321-4667-b815-b576ea5f22d2), [On](https://mobbin.com/screens/e3b8cd54-6440-4689-9d33-8514ee78c65a)).
These have a past-tense headline ("Order received"), a horizontal step tracker (Received → Preparing → Ready) and "we've emailed you".
→ The done screen gets "You're on the list! 🎤", a tracker reading Sent ✓ → Being made (~30 min) → Ready to sing, and "We'll email the video to anya@…". It is never confusable with the make screen, and there's no ambiguity about whether it was sent.
