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
# Step 2: fetch lyrics from LRCLIB
# --------------------------------------------------------------------------- #
def _strip_lrc_timestamps(synced: str) -> str:
    """Turn `[mm:ss.xx] line` synced lyrics into plain text lines."""
    lines = []
    for raw in synced.splitlines():
        line = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", raw).strip()
        lines.append(line)
    return "\n".join(lines).strip()


def fetch_lyrics(artist: str, title: str, duration: float | None) -> str | None:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    # 1) Exact get (best match — uses duration when available).
    params = {"artist_name": artist, "track_name": title}
    if duration:
        params["duration"] = int(round(duration))
    try:
        r = session.get(f"{LRCLIB_BASE}/get", params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            body = data.get("plainLyrics") or (
                _strip_lrc_timestamps(data["syncedLyrics"]) if data.get("syncedLyrics") else None
            )
            if body:
                return body
    except requests.RequestException as exc:
        log(f"LRCLIB get failed: {exc}")

    # 2) Fallback search — take the first hit that actually has lyrics.
    try:
        r = session.get(f"{LRCLIB_BASE}/search", params={"q": f"{artist} {title}"}, timeout=15)
        if r.status_code == 200:
            for hit in r.json():
                body = hit.get("plainLyrics") or (
                    _strip_lrc_timestamps(hit["syncedLyrics"]) if hit.get("syncedLyrics") else None
                )
                if body:
                    return body
    except requests.RequestException as exc:
        log(f"LRCLIB search failed: {exc}")

    return None


# --------------------------------------------------------------------------- #
# Step 3: build the crawl text + render with ffmpeg
# --------------------------------------------------------------------------- #
def build_crawl_lines(artist: str, title: str, lyrics: str, wrap: int) -> list[str]:
    wrapped: list[str] = []
    for line in lyrics.splitlines():
        line = line.strip()
        if not line:
            wrapped.append("")  # preserve stanza breaks
            continue
        wrapped.extend(textwrap.wrap(line, width=wrap) or [""])

    header = [artist.upper(), title.upper(), "", ""]
    # A few leading blanks so the crawl eases in from below the frame,
    # and trailing blanks so it fully clears the top.
    return ["", ""] + header + wrapped + ["", "", ""]


def render_crawl_png(
    lines: list[str], width: int, fontsize: int, font_path: str, workdir: str
) -> tuple[str, int]:
    """Render the whole crawl to one tall RGBA PNG with PIL.

    We rasterise with PIL rather than ffmpeg's drawtext because ffmpeg 8's
    always-on harfbuzz shaping renders a `.notdef` box for every newline in a
    multi-line textfile. PIL also gives clean per-line centering and a stroke
    outline, and leaves room to add a Star Wars perspective later.
    """
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, fontsize)
    ascent, descent = font.getmetrics()
    line_advance = ascent + descent + round(fontsize * 0.35)
    stroke = max(2, fontsize // 16)
    pad = fontsize  # keep first/last lines (and their stroke) off the edges

    height = pad * 2 + line_advance * len(lines)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = pad
    for line in lines:
        if line:
            line_w = draw.textlength(line, font=font)
            x = (width - line_w) / 2
            draw.text(
                (x, y), line, font=font,
                fill=(255, 232, 31, 255),          # Star Wars crawl yellow
                stroke_width=stroke, stroke_fill=(0, 0, 0, 255),
            )
        y += line_advance

    png_path = os.path.join(workdir, "crawl.png")
    img.save(png_path)
    return png_path, height


def render_video(
    instrumental: str,
    crawl_png: str,
    duration: float,
    out_path: str,
    width: int,
    height: int,
) -> None:
    # Constant-rate upward crawl: the PNG travels from just below the frame
    # (y=H) to fully above it (y=-h), a distance of (H+h), across the full song
    # duration. W/H = background dims, w/h = overlay (PNG) dims, t = timestamp.
    y_expr = f"H-(t)*((H+h)/{duration:.3f})"
    filt = f"[0:v][1:v]overlay=x=(W-w)/2:y={y_expr}[v]"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=24",
        "-loop", "1", "-framerate", "24", "-i", crawl_png,
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

        # 2) Lyrics
        if args.lyrics_file:
            with open(args.lyrics_file, encoding="utf-8") as fh:
                lyrics = fh.read()
            log(f"lyrics: loaded {len(lyrics)} chars from {args.lyrics_file}")
        else:
            t = Timer.begin("fetch lyrics (LRCLIB)")
            lyrics = fetch_lyrics(args.artist, args.title, duration)
            t.done()
            if not lyrics:
                log("No lyrics found on LRCLIB — cannot build a lyrics crawl. Aborting.")
                return 1
            log(f"  lyrics: {len(lyrics.splitlines())} lines, {len(lyrics)} chars")

        # 3) Render
        width = round(args.height * 16 / 9)
        width += width % 2  # ffmpeg needs even dimensions
        fontsize = max(18, round(args.height * 0.07))
        lines = build_crawl_lines(args.artist, args.title, lyrics, args.wrap)

        t = Timer.begin("rasterise crawl (PIL)")
        crawl_png, _png_h = render_crawl_png(lines, width, fontsize, args.font, workdir)
        t.done()

        t = Timer.begin(f"render {args.height}p crawl video (ffmpeg)")
        render_video(instrumental, crawl_png, duration, out_path, width, args.height)
        t.done()

        elapsed = total.done()
        size_mb = os.path.getsize(out_path) / 1e6
        log(f"DONE → {out_path} ({size_mb:.1f} MB) in {elapsed:.1f}s wall-clock")
        return 0
    finally:
        if args.keep_temp:
            log(f"kept working dir: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
