# YouTube circuit breaker + quieter singer copy + compact My songs cards (2026-10-29)

## Andrew's request (verbatim)

> i think we shouldn't show this "Almost always works — YouTube downloads occasionally fail" or "Definitely
> available — works even offline" messaging to singers: [screenshot]
> instead, we should assume youtube downloads will work (no need to confuse or worry singers unnecessarily) but
> then if a youtube download fails even after a retry, the system should detect that and stop offering tracks from
> youtube for the rest of the night, perhaps periodically re-trying every 30 mins or something and re-enabling
> youtube sourced videos if yt-dlp starts working again?
> that way, worst case scenario is a single singer has a bad experience where the version of a song they chose may
> have to be replaced with another non-youtube version, but the exposure of negative repercussions from that
> scenario is limited
>
> additionally, on this screen in the singer ui [screenshot] each song card seems larger than necessary - i feel
> like we could change the layout of the info and buttons on song cards like this [screenshot] to make them
> vertically more compact with less wasted space

## Design

### 1. Singer copy
Remove the version-row availability line (`versions.sourceLocal/sourceCloud/sourceCloudNoSize/sourceYouTube`) and
the open song row's amber `song-risk` line. Delete those four keys from all locales.

### 2. YouTube breaker (`kj-controller/youtube_breaker.py`)
State `closed` / `open`, persisted in `rotation_meta` (`youtube_breaker`), so a restart mid-show keeps it.

- **Trip:** a YouTube download fails with a *transient* (yt-dlp/network/bot-check) error and its bounded retries are
  exhausted. A video-gone ("unavailable") error is about that one video and does NOT trip it.
- **Success** of any YouTube download closes it.
- **Probe:** while open, a daemon thread re-tries a real download (the URL that tripped it) every 30 min
  (`youtube_breaker_probe_interval_s`, default 1800). Success closes the breaker and the file lands in the library.
- **While open:**
  - Singer search (`/sing/search`, `/sing/search/resolve`, and through them app v1) drops YouTube-only versions
    (KN without a Divebar file) and any song left with none.
  - `kj_pick` auto-binding prefers the best non-YouTube version.
  - The download worker skips a YouTube attempt for a sing request that has a local/Divebar alternative.
- **Cross-source fallback** (the documented v1 follow-up): when a sing request's YouTube download can't be resolved
  (breaker open or all YouTube candidates exhausted), rebind it to the best local or Divebar version from its
  `versions[]` snapshot: local → link the file; Divebar → re-queue as a Divebar download. The singer gets the
  existing `resolved_alt` push.
- KJ visibility: `/youtube/status` gains a `breaker` block; trip/close are logged.

### 3. Compact My songs card
`.song-card` wraps: the text takes the full width and the actions become a single wrapping row of small buttons
underneath, instead of a 5-high column on the right.
