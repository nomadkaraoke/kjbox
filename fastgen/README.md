# fastgen — ultrafast, low-cost, on-demand karaoke (proof of concept)

Seed of the **"Ultrafast, low-cost, on-demand karaoke generation"** backlog item.
Given **artist + title** (and optionally an audio file), it produces a low-res
(480p) "karaoke" video as fast as possible, mostly locally:

0. **Fetch audio** if no file is given — via `flacfetch-remote` (the same remote
   flacfetch API karaoke-gen uses; needs `FLACFETCH_API_URL`/`KEY`). Or pass a
   file, or a `--url` (YouTube/any yt-dlp site).
1. **Separate the instrumental** with a *single fast* audio-separator model
   (no slow ensemble) — default `UVR-MDX-NET-Inst_HQ_4.onnx` (writes both stems).
2. **Get lyrics + timing** (see "Lyric sync" below).
3. **Render** the lyrics scrolling upward (**Star Wars crawl**) over a solid
   background, muxed with the instrumental, in a single ffmpeg pass.

Deliberately primitive: no lyrics review, no cloud round-trips. Optimised for
speed and "good enough" sync.

### Lyric sync (tiered)

The hard part is getting each line on screen *when it's actually sung* without a
full AudioShake/forced-alignment pass. fastgen tries, in order:

1. **Synced lyrics (implemented).** LRCLIB returns line-level `[mm:ss.xx]`
   timestamps for most popular songs — and you can paste an `.lrc` from anywhere
   into `--lyrics-file` (auto-detected). Each line is *time-anchored*: it reaches
   a fixed on-screen reading position exactly at its timestamp. The scroll rate
   varies between lines (held lines linger, quick lines fly, instrumental gaps
   pause) — driven by a piecewise-linear ffmpeg `overlay y` expression.
2. **Forced alignment (implemented).** When only *plain* lyrics exist (LRCLIB
   plain, or a pasted `--lyrics-file`), we align the known text to the separated
   **vocal stem** with whisper: it transcribes the vocals with word timestamps,
   we match what it *heard* to the lyric words we *know* (difflib), and give each
   line the time of its earliest matched word (unmatched lines interpolate). Uses
   the already-installed `openai-whisper` — no torchaudio. This is the path for
   niche songs. **Segment-level by default** (fast: ~15s for a 3-min song with
   `--whisper-model base`; words spread linearly within each whisper segment —
   coarse but fine since several lines are always on screen). `--precise-align`
   switches to per-word DTW timestamps (3-5× slower). Only runs off the synced path.
3. **Constant crawl (fallback).** No lyrics timing and alignment unavailable/failed
   → scroll the whole block at a constant rate across the song (true Star Wars).

## Run (simple wrapper — for live use)

The `fastgen` wrapper takes positional **artist, title, audio** and writes the
mp4 into the current folder. It auto-selects the right Python env, so you can run
it from anywhere:

```bash
# no audio file → auto-fetches the audio via flacfetch (needs the workspace .envrc)
/path/to/fastgen/fastgen "ABBA" "Waterloo"

# or give it an audio file:
fastgen "ABBA" "Waterloo" ~/Downloads/waterloo.flac

# or a URL (YouTube / any yt-dlp site) — fastest fetch:
fastgen "ABBA" "Waterloo" https://youtu.be/VIDEO_ID

# explicit output path / higher res for a projector:
fastgen "ABBA" "Waterloo" ~/Downloads/waterloo.flac ~/Desktop/out.mp4
fastgen "ABBA" "Waterloo" --height 720
```

Auto-fetch needs `FLACFETCH_API_URL` + `FLACFETCH_API_KEY` in the environment
(loaded from the workspace `.envrc` via direnv).

Tip: symlink it onto your PATH once — `ln -s "$PWD/fastgen" /usr/local/bin/fastgen`
— then just `fastgen "Artist" "Title" file`. Takes ~1 min for a 3-min song on a
laptop; then upload the mp4 to kjbox yourself.

## Run (direct — for iterating)

```bash
# From the nomadkaraoke conda env (has audio-separator + ffmpeg)
python fastgen.py path/to/audio.flac --artist "ABBA" --title "Waterloo"

# Iterate on the render only (skip the slow separation step):
python fastgen.py audio.flac --artist ABBA --title Waterloo --skip-separation

# Niche song: paste lyrics you found online (plain text OR .lrc) — plain text is
# auto-timed via whisper alignment; .lrc is used directly:
python fastgen.py audio.flac --artist X --title Y --lyrics-file lyrics.txt
python fastgen.py audio.flac --artist X --title Y --lyrics-file lyrics.txt --whisper-model small --lang es
```

Useful flags: `--url` (fetch a specific URL), `--model` (separator model),
`--height` (default 480), `--wrap` (chars/line), `--reading` (active-line
position, default 0.42), `--whisper-model` (tiny/base/small/medium),
`--precise-align` (per-word timing, slower), `--lang`, `--no-align`, `--fps`,
`--font`, `--out`, `--keep-temp`.

## Measured (ABBA – Waterloo, 2:45, on an M-series laptop **CPU**)

| Stage | Time |
|---|---|
| probe duration | 0.1s |
| separate instrumental (single MDX model) | **41.6s** |
| fetch lyrics (LRCLIB) | 0.3s |
| rasterise crawl (PIL) | 0.2s |
| render 480p video (ffmpeg, ~17× realtime) | 9.7s |
| **total** | **51.8s** |

Separation dominates. On the L4 GPU (the existing `audio-separator` Cloud Run
service, or a hot instance) it drops to seconds → **sub-30s total is realistic**.

## Design notes / gotchas

- **Render is PIL → PNG → ffmpeg `overlay` scroll**, *not* ffmpeg `drawtext`.
  ffmpeg 8's always-on harfbuzz shaping renders a `.notdef` box for every
  newline in a multi-line `textfile`, and the `text_shaping` toggle was removed.
  Rasterising the whole crawl with PIL sidesteps that, gives clean per-line
  centering + a stroke outline, and leaves room for a real 3D perspective later.
- Lyrics are wrapped to `--wrap` chars (PIL/drawtext don't auto-wrap).
- Single fast model via `Separator(output_single_stem="Instrumental")` — only
  the instrumental stem is written.

## Where this is heading (backlog vision)

- **GPU separation**: route step 1 to the existing `audio-separator` Cloud Run
  L4 service (or a hot/min-instances=1 instance) instead of local CPU. Add a
  `--source-url` (YouTube) ingest to the separator so a KJ box only has to
  *download* the finished instrumental (one-way transfer).
- **On-device home = kjbox**: wrap this as a "Fast generate" action in the
  kj-controller (reuses mpv for instant playback, the generic downloader, and
  on-device ffmpeg).
- **Cheap gen API tier**: expose as a ~$1/track "draft" tier in karaoke-gen
  (gen already has an LRCLIB client + ffmpeg/libass render to reuse).
- **Faster alignment**: whisper `base` is ~1× real-time on CPU. Options — try
  segment-level timing (drop `word_timestamps`, ~3-5× faster, coarser), MPS/GPU,
  or a hot cloud aligner. Only matters for the niche fallback path.
- **VAD-guided fallback**: if alignment matches too few lines, distribute lines
  across vocal-active regions (detected from the stem) instead of a blind crawl.
- **Star Wars perspective**: add a `perspective`/`v360`-style tilt so the crawl
  recedes toward the top.
- **Fewer lines / bigger text**: the current reading window shows ~8 lines; a
  tighter window may read better on a projector.
