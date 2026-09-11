#!/usr/bin/env python3
"""fastgen — ultrafast, minimal local karaoke video generator (proof of concept).

Given input audio + artist + title, produce a low-res (480p) "karaoke" video as
fast as possible on the local machine:

  1. Separate the instrumental with a SINGLE fast audio-separator model
     (no slow ensemble).
  2. Fetch lyrics from the internet (LRCLIB — free, no API key).
  3. Render the lyrics scrolling upward (Star Wars crawl) over a solid
     background, muxed with the instrumental audio, via a single ffmpeg pass.

This is deliberately primitive: no precise per-word timing, no lyrics review,
no cloud round-trips. The scroll is a constant-rate crawl paced across the song
duration — "good enough" sync, optimised for speed. It is the seed of the
"Ultrafast, low-cost, on-demand karaoke generation" backlog item; the eventual
home is on-device in kjbox (and/or a cheap gen API tier).

Usage:
    python fastgen.py AUDIO --artist "ABBA" --title "Waterloo"
    python fastgen.py AUDIO --artist "ABBA" --title "Waterloo" --out out.mp4

Requires: ffmpeg/ffprobe on PATH, the `audio-separator` package, `requests`.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass

import requests

LRCLIB_BASE = "https://lrclib.net/api"
# A single, fast instrumental model (no ensemble). MDX-Net Inst HQ 4 is a good
# speed/quality trade; "2_HP-UVR.pth" (VR arch) is an even lighter alternative.
DEFAULT_MODEL = "UVR-MDX-NET-Inst_HQ_4.onnx"
DEFAULT_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
# Star Wars opening-crawl yellow.
CRAWL_COLOR = "0xFFE81F"
USER_AGENT = "nomad-fastgen-poc/0.1 (https://nomadkaraoke.com)"


def log(msg: str) -> None:
    print(f"[fastgen] {msg}", flush=True)


@dataclass
class Timer:
    """Tiny stage timer for eyeballing where the seconds go."""

    label: str
    start: float

    @classmethod
    def begin(cls, label: str) -> "Timer":
        log(f"→ {label} …")
        return cls(label, time.monotonic())

    def done(self) -> float:
        elapsed = time.monotonic() - self.start
        log(f"✓ {self.label} ({elapsed:.1f}s)")
        return elapsed


# --------------------------------------------------------------------------- #
# Step 0: probe duration
# --------------------------------------------------------------------------- #
def probe_duration(audio_path: str) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1",
            audio_path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- #
# Step 1: separate instrumental (single fast model)
# --------------------------------------------------------------------------- #
def separate_instrumental(audio_path: str, workdir: str, model: str) -> str:
    # Imported lazily so `--skip-separation` works without the heavy dep loaded.
    from audio_separator.separator import Separator

    sep = Separator(
        output_dir=workdir,
        output_format="WAV",
        output_single_stem="Instrumental",  # only write the instrumental stem
    )
    sep.load_model(model_filename=model)
    outputs = sep.separate(audio_path)

    # `separate()` returns basenames (recent versions) or paths; resolve robustly.
    candidates = []
    for name in outputs or []:
        candidates.append(name if os.path.isabs(name) else os.path.join(workdir, name))
    candidates += glob.glob(os.path.join(workdir, "*Instrumental*.wav"))
    candidates = [c for c in candidates if os.path.exists(c)]
    if not candidates:
        raise RuntimeError(
            f"Separation produced no instrumental file in {workdir}. "
            f"Model output was: {outputs!r}"
        )
    # Prefer a file whose name mentions Instrumental.
    inst = next((c for c in candidates if "instrumental" in os.path.basename(c).lower()), candidates[0])
    return inst


# --------------------------------------------------------------------------- #
# Step 2: fetch lyrics from LRCLIB (prefer time-synced)
# --------------------------------------------------------------------------- #
@dataclass
class Lyrics:
    """Fetched lyrics. `timed` holds (start_seconds, line) pairs when the source
    provided synced (LRC) lyrics; otherwise it is None and only `plain` is set."""

    kind: str                                        # "synced" | "plain"
    plain: str
    timed: "list[tuple[float, str]] | None" = None


_LRC_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def _strip_lrc_timestamps(synced: str) -> str:
    return "\n".join(_LRC_RE.sub("", raw).strip() for raw in synced.splitlines()).strip()


def _parse_synced(synced: str) -> list[tuple[float, str]]:
    """Parse `[mm:ss.xx] line` LRC into sorted (start_seconds, text) pairs."""
    out: list[tuple[float, str]] = []
    for raw in synced.splitlines():
        m = _LRC_RE.match(raw)
        if not m:
            continue
        start = int(m.group(1)) * 60 + float(m.group(2))
        out.append((start, _LRC_RE.sub("", raw).strip()))
    out.sort(key=lambda p: p[0])
    return out


def _lyrics_from_payload(data: dict) -> "Lyrics | None":
    synced = data.get("syncedLyrics")
    if synced and synced.strip():
        return Lyrics("synced", _strip_lrc_timestamps(synced), _parse_synced(synced))
    plain = data.get("plainLyrics")
    if plain and plain.strip():
        return Lyrics("plain", plain)
    return None


def fetch_lyrics(artist: str, title: str, duration: float | None) -> "Lyrics | None":
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    # 1) Exact get (best match — uses duration when available).
    params = {"artist_name": artist, "track_name": title}
    if duration:
        params["duration"] = int(round(duration))
    try:
        r = session.get(f"{LRCLIB_BASE}/get", params=params, timeout=15)
        if r.status_code == 200:
            got = _lyrics_from_payload(r.json())
            if got:
                return got
    except requests.RequestException as exc:
        log(f"LRCLIB get failed: {exc}")

    # 2) Fallback search — first hit with lyrics, preferring synced results.
    try:
        r = session.get(f"{LRCLIB_BASE}/search", params={"q": f"{artist} {title}"}, timeout=15)
        if r.status_code == 200:
            for hit in sorted(r.json(), key=lambda h: 0 if h.get("syncedLyrics") else 1):
                got = _lyrics_from_payload(hit)
                if got:
                    return got
    except requests.RequestException as exc:
        log(f"LRCLIB search failed: {exc}")

    return None


# --------------------------------------------------------------------------- #
# Step 3: lay out the crawl, rasterise it, and build the time-anchored scroll
# --------------------------------------------------------------------------- #
# A "visual line" is one rendered row of text plus an optional anchor time — the
# moment that line should reach the on-screen reading position.
VisualLine = "tuple[str, float | None]"


def build_visual_lines(artist: str, title: str, lyrics: Lyrics, wrap: int) -> list:
    header = [("", None), ("", None),
              (artist.upper(), None), (title.upper(), None),
              ("", None), ("", None)]
    body: list = []
    if lyrics.kind == "synced" and lyrics.timed:
        for start, text in lyrics.timed:
            text = text.strip()
            if not text:
                continue  # instrumental gap — the scroll naturally lingers here
            wrapped = textwrap.wrap(text, width=wrap) or [""]
            for i, w in enumerate(wrapped):
                body.append((w, start if i == 0 else None))  # anchor first wrap-row
    else:
        for raw in lyrics.plain.splitlines():
            raw = raw.strip()
            if not raw:
                body.append(("", None))
                continue
            body.extend((w, None) for w in (textwrap.wrap(raw, width=wrap) or [""]))
    return header + body + [("", None)] * 3


def render_crawl_png(
    lines: list, width: int, fontsize: int, font_path: str, workdir: str
) -> tuple[str, int, list]:
    """Rasterise the whole crawl to one tall PNG; return (path, height, centers).

    `centers[i]` is the vertical centre (px, image coords) of visual line i, used
    to anchor lines to their sung time. We rasterise with PIL rather than ffmpeg
    drawtext because ffmpeg 8's always-on harfbuzz shaping renders a `.notdef`
    box for every newline in a multi-line textfile.
    """
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, fontsize)
    ascent, descent = font.getmetrics()
    line_advance = ascent + descent + round(fontsize * 0.35)
    stroke = max(2, fontsize // 16)
    pad = fontsize  # keep first/last lines (and their stroke) off the edges

    height = pad * 2 + line_advance * len(lines)
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    centers: list = []
    y = pad
    for text, _anchor in lines:
        if text:
            line_w = draw.textlength(text, font=font)
            draw.text(
                ((width - line_w) / 2, y), text, font=font,
                fill=(255, 232, 31),                     # Star Wars crawl yellow
                stroke_width=stroke, stroke_fill=(0, 0, 0),
            )
        centers.append(y + line_advance / 2)
        y += line_advance

    png_path = os.path.join(workdir, "crawl.png")
    img.save(png_path)
    return png_path, height, centers


def build_scroll_y_expr(
    lines: list, centers: list, duration: float,
    frame_h: int, img_h: int, reading_frac: float,
) -> tuple[str, bool]:
    """Build the ffmpeg `overlay` y expression; return (expr, is_time_anchored).

    Convention: y = position of the PNG's top edge relative to the frame top, so
    image row `c` shows at frame row `y + c`. We want each anchored line's centre
    `c_i` to sit at the reading row `R` at its time `t_i`, i.e. y(t_i) = R - c_i.
    Between anchors the scroll is piecewise-linear (rate varies, so held lines
    linger and quick lines fly past). With no anchors we fall back to a constant
    crawl across the whole song.
    """
    reading_row = reading_frac * frame_h
    pts = [(0.0, float(frame_h))]  # t=0: PNG fully below the frame (nothing shown yet)
    for (_text, anchor), c in zip(lines, centers):
        if anchor is not None and 0.0 < anchor < duration:
            pts.append((float(anchor), reading_row - c))

    timed = len(pts) > 1
    if timed:
        pts.append((duration, pts[-1][1]))       # freeze on the last line to the end
    else:
        pts.append((duration, -float(img_h)))    # constant crawl fully off the top

    # Keep strictly-increasing time breakpoints.
    clean = [pts[0]]
    for t, y in pts[1:]:
        if t > clean[-1][0] + 1e-3:
            clean.append((t, y))
    pts = clean

    # Continuous piecewise-linear scroll as a flat sum of ramps (no nested ifs,
    # evaluated once per frame): y = Y0 + M0*(t-T0) + Σ (Mk - M[k-1])*max(0,t-Tk).
    # Commas inside max() are escaped (\,) for the filtergraph parser.
    slopes = [(pts[i + 1][1] - pts[i][1]) / (pts[i + 1][0] - pts[i][0]) for i in range(len(pts) - 1)]
    terms = [f"{pts[0][1]:.2f}", f"({slopes[0]:.5f})*(t-{pts[0][0]:.3f})"]
    for k in range(1, len(slopes)):
        dm = slopes[k] - slopes[k - 1]
        if abs(dm) < 1e-6:
            continue
        terms.append(f"({dm:.5f})*max(0\\,t-{pts[k][0]:.3f})")
    return "+".join(terms), timed


def render_video(
    instrumental: str, crawl_png: str, y_expr: str,
    out_path: str, width: int, height: int, fps: int,
) -> None:
    filt = f"[0:v][1:v]overlay=x=(W-w)/2:y={y_expr}[v]"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}",
        "-loop", "1", "-framerate", str(fps), "-i", crawl_png,
        "-i", instrumental,
        "-filter_complex", filt,
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Ultrafast minimal local karaoke video POC")
    ap.add_argument("audio", help="Input audio file (any ffmpeg-readable format)")
    ap.add_argument("--artist", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", help="Output mp4 path (default: '<Artist> - <Title> (Fastgen).mp4')")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"audio-separator model (default: {DEFAULT_MODEL})")
    ap.add_argument("--height", type=int, default=480, help="Output height in px (default: 480)")
    ap.add_argument("--wrap", type=int, default=34, help="Max characters per lyric line before wrapping")
    ap.add_argument("--reading", type=float, default=0.42,
                    help="Vertical reading position for the active line (0=top, 1=bottom; default 0.42)")
    ap.add_argument("--fps", type=int, default=24, help="Output frame rate (default: 24)")
    ap.add_argument("--font", default=DEFAULT_FONT, help="Path to a .ttf font")
    ap.add_argument("--lyrics-file", help="Use this local lyrics .txt instead of fetching from LRCLIB")
    ap.add_argument("--skip-separation", action="store_true",
                    help="Use the input audio directly as the 'instrumental' (for iterating on the render)")
    ap.add_argument("--keep-temp", action="store_true", help="Keep the working directory")
    args = ap.parse_args(argv)

    if not os.path.exists(args.audio):
        log(f"Input audio not found: {args.audio}")
        return 2
    if not os.path.exists(args.font):
        log(f"Font not found: {args.font}")
        return 2

    out_path = args.out or f"{args.artist} - {args.title} (Fastgen).mp4"
    workdir = tempfile.mkdtemp(prefix="fastgen-")
    total = Timer.begin(f"fastgen '{args.artist} - {args.title}'")

    try:
        t = Timer.begin("probe duration")
        duration = probe_duration(args.audio)
        t.done()
        log(f"  duration: {duration:.1f}s")

        # 1) Instrumental
        if args.skip_separation:
            log("skip-separation: using input audio as the instrumental track")
            instrumental = args.audio
        else:
            t = Timer.begin(f"separate instrumental (model={args.model})")
            instrumental = separate_instrumental(args.audio, workdir, args.model)
            t.done()
            log(f"  instrumental: {instrumental}")

        # 2) Lyrics (prefer time-synced)
        if args.lyrics_file:
            with open(args.lyrics_file, encoding="utf-8") as fh:
                lyrics = Lyrics("plain", fh.read())
            log(f"lyrics: loaded plain lyrics from {args.lyrics_file}")
        else:
            t = Timer.begin("fetch lyrics (LRCLIB)")
            lyrics = fetch_lyrics(args.artist, args.title, duration)
            t.done()
            if not lyrics:
                log(f"No lyrics found on LRCLIB for '{args.artist} - {args.title}'.")
                log("Try a different spelling of the artist/title, or pass --lyrics-file.")
                return 1
            if lyrics.kind == "synced":
                log(f"  lyrics: SYNCED — {len(lyrics.timed)} timed lines (time-anchored scroll)")
            else:
                log(f"  lyrics: PLAIN — {len(lyrics.plain.splitlines())} lines (no timing → constant crawl)")

        # 3) Render
        width = round(args.height * 16 / 9)
        width += width % 2  # ffmpeg needs even dimensions
        fontsize = max(18, round(args.height * 0.07))
        lines = build_visual_lines(args.artist, args.title, lyrics, args.wrap)

        t = Timer.begin("rasterise crawl (PIL)")
        crawl_png, img_h, centers = render_crawl_png(lines, width, fontsize, args.font, workdir)
        t.done()

        y_expr, timed = build_scroll_y_expr(lines, centers, duration, args.height, img_h, args.reading)
        log(f"  scroll: {'time-anchored (synced timestamps)' if timed else 'constant crawl'}")

        t = Timer.begin(f"render {args.height}p crawl video (ffmpeg)")
        render_video(instrumental, crawl_png, y_expr, out_path, width, args.height, args.fps)
        t.done()

        elapsed = total.done()
        size_mb = os.path.getsize(out_path) / 1e6
        log(f"DONE → {os.path.abspath(out_path)} ({size_mb:.1f} MB) in {elapsed:.1f}s wall-clock")
        return 0
    finally:
        if args.keep_temp:
            log(f"kept working dir: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
