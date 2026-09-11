# fastgen — ultrafast, low-cost, on-demand karaoke (proof of concept)

Seed of the **"Ultrafast, low-cost, on-demand karaoke generation"** backlog item.
Given input **audio + artist + title**, it produces a low-res (480p) "karaoke"
video as fast as possible, entirely locally:

1. **Separate the instrumental** with a *single fast* audio-separator model
   (no slow ensemble) — default `UVR-MDX-NET-Inst_HQ_4.onnx`.
2. **Fetch lyrics** from the internet (LRCLIB — free, no API key).
3. **Render** the lyrics scrolling upward (**Star Wars crawl**) over a solid
   background, muxed with the instrumental, in a single ffmpeg pass.

Deliberately primitive: no lyrics review, no cloud round-trips. Optimised for
speed and "good enough" sync.

### Lyric sync (tiered)

The hard part is getting each line on screen *when it's actually sung* without a
full AudioShake/forced-alignment pass. fastgen tries, in order:

1. **Synced lyrics (implemented).** LRCLIB returns line-level `[mm:ss.xx]`
   timestamps for most popular songs. Each line is *time-anchored*: it reaches a
   fixed on-screen reading position exactly at its timestamp. The scroll rate
   varies between lines (held lines linger, quick lines fly, instrumental gaps
   pause) — driven by a piecewise-linear ffmpeg `overlay y` expression.
2. **Forced alignment (planned).** When only plain lyrics exist, align the known
   text to the vocal stem (already produced in step 1) with a lightweight CTC
   aligner (torchaudio Wav2Vec2 / MMS_FA — much lighter than Whisper/MFA) to
   synthesise line/word timestamps, then feed the same time-anchored scroll.
3. **Constant crawl (fallback).** No timing available → scroll the whole block at
   a constant rate across the song duration (true Star Wars style).

## Run (simple wrapper — for live use)

The `fastgen` wrapper takes positional **artist, title, audio** and writes the
mp4 into the current folder. It auto-selects the right Python env, so you can run
it from anywhere:

```bash
# writes "ABBA - Waterloo (Fastgen).mp4" into the current directory
/path/to/fastgen/fastgen "ABBA" "Waterloo" ~/Downloads/waterloo.flac

# explicit output path:
fastgen "ABBA" "Waterloo" ~/Downloads/waterloo.flac ~/Desktop/waterloo.mp4

# higher res for a projector:
fastgen "ABBA" "Waterloo" ~/Downloads/waterloo.flac --height 720
```

Tip: symlink it onto your PATH once — `ln -s "$PWD/fastgen" /usr/local/bin/fastgen`
— then just `fastgen "Artist" "Title" file`. Takes ~1 min for a 3-min song on a
laptop; then upload the mp4 to kjbox yourself.

## Run (direct — for iterating)

```bash
# From the nomadkaraoke conda env (has audio-separator + ffmpeg)
python fastgen.py path/to/audio.flac --artist "ABBA" --title "Waterloo"

# Iterate on the render only (skip the slow separation step):
python fastgen.py audio.flac --artist ABBA --title Waterloo --skip-separation

# Use your own lyrics text instead of LRCLIB (plain text; constant crawl):
python fastgen.py audio.flac --artist ABBA --title Waterloo --lyrics-file lyrics.txt
```

Useful flags: `--model` (separator model), `--height` (default 480), `--wrap`
(chars/line before wrapping), `--reading` (active-line position, default 0.42),
`--fps`, `--font`, `--out`, `--keep-temp`.

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
- **Tier 2 forced alignment**: CTC alignment on the vocal stem for songs without
  LRCLIB synced lyrics (see "Lyric sync" above).
- **Star Wars perspective**: add a `perspective`/`v360`-style tilt so the crawl
  recedes toward the top.
- **Fewer lines / bigger text**: the current reading window shows ~8 lines; a
  tighter window may read better on a projector.
