# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.40"]
# ///
"""Render a themed event ad (portrait 1080x1350 + square 1080x1080 PNGs) from an event.json.

    uv run render.py events/2026-11-26-thanksgiving
    uv run render.py events/2026-11-26-thanksgiving --copy-to ~/Downloads

Writes <event dir>/<slug>-portrait.png and <slug>-square.png. Uses the installed Google Chrome
(no Playwright browser download needed). Emoji art is Google's Noto Emoji (Apache 2.0), fetched once
into assets/emoji/ and committed, so renders are reproducible offline apart from Google Fonts.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote_plus

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
EMOJI_DIR = ASSETS / "emoji"
NOTO_URL = "https://cdn.jsdelivr.net/gh/googlefonts/noto-emoji@main/2D/svg/emoji_u{code}.svg"
VARIANTS = {"portrait": 1350, "square": 1080}

DEFAULT_THEME = {
    "title_font": "Shrikhand",
    "sticker_font": "Bangers",
    "body_font": "Poppins",
    "title_gradient": ["#ffe08a", "#ffb84a", "#f08a2a"],
    "shadow": "#5b1a10",
    "accent": "#ffc95c",
    "date_color": "#ff7acc",
    "sticker_color": "#ffffff",
    "curtain": ["#c2541b", "#8a2a1c", "#4a1424", "#2a0d1e", "#1b0915"],
    "swag": ["#e0782a", "#a33a1a"],
    "panel": ["rgba(20,6,14,0.82)", "rgba(20,6,14,0.55)"],
    "quip_bg": "rgba(91,26,16,0.75)",
    "quip_border": "rgba(255,201,92,0.55)",
    "background_image": None,
}
REQUIRED = ("title", "venue", "date", "features")


class EventError(ValueError):
    pass


def emoji_code(value: str) -> str:
    """Accept an emoji character ("🦃"), a codepoint ("1f983", "U+1F983") or a sequence
    ("1f468_200d_1f3a4") and return Noto's file code. FE0F (variation selector) is dropped,
    as Noto's filenames omit it."""
    v = value.strip()
    if re.fullmatch(r"(?i)(u\+)?[0-9a-f]{2,6}([_ -](u\+)?[0-9a-f]{2,6})*", v):
        parts = re.split(r"[_ -]", v.lower().replace("u+", ""))
    else:
        parts = [f"{ord(ch):x}" for ch in v]
    parts = [f"{int(p, 16):04x}" for p in parts if p != "fe0f"]  # Noto pads to 4 digits: 0031_20e3
    if not parts:
        raise EventError(f"not an emoji: {value!r}")
    return "_".join(parts)


def ensure_emoji(value: str, fetch=None) -> Path:
    code = emoji_code(value)
    path = EMOJI_DIR / f"{code}.svg"
    if not path.exists():
        fetch = fetch or _download
        EMOJI_DIR.mkdir(parents=True, exist_ok=True)
        data = fetch(NOTO_URL.format(code=code))
        if not data.lstrip().startswith(b"<svg") and b"<svg" not in data[:300]:
            raise EventError(f"Noto has no SVG for {value!r} ({code})")
        path.write_bytes(data)
    return path


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "event-ads-renderer"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        raise EventError(f"couldn't download {url}: {e}") from e


def load_event(event_dir: Path, fetch=None) -> dict:
    """Read event.json, fill theme defaults and resolve every asset to a file:// URI."""
    cfg_path = event_dir / "event.json"
    if not cfg_path.exists():
        raise EventError(f"{cfg_path} not found")
    ev = json.loads(cfg_path.read_text())
    missing = [k for k in REQUIRED if not ev.get(k)]
    if missing:
        raise EventError(f"event.json is missing: {', '.join(missing)}")
    ev.setdefault("slug", event_dir.name)
    ev["theme"] = {**DEFAULT_THEME, **ev.get("theme", {})}
    bg = ev["theme"].get("background_image")
    if bg:
        p = (event_dir / bg).resolve()
        if not p.exists():
            raise EventError(f"background_image {bg} not found in {event_dir}")
        ev["theme"]["background_image"] = p.as_uri()
    partner = ev.get("partner_logo") or str(ASSETS / "babylon-kino-logo.png")
    pp = (event_dir / partner).resolve() if not Path(partner).is_absolute() else Path(partner)
    if not pp.exists():
        raise EventError(f"partner_logo {partner} not found")
    ev["partner_logo"] = pp.as_uri()
    for f in ev["features"]:
        if "text" not in f or "icon" not in f:
            raise EventError(f"each feature needs icon + text: {f}")
        f["icon_src"] = ensure_emoji(f["icon"], fetch).as_uri()
        if f.get("overlay"):
            f["overlay_src"] = ensure_emoji(f["overlay"], fetch).as_uri()
    for d in ev.get("decorations", []):
        d["src"] = ensure_emoji(d["emoji"], fetch).as_uri() if "emoji" in d else (event_dir / d["image"]).resolve().as_uri()
    return ev


def fonts_href(theme: dict) -> str:
    fams = []
    for key, spec in (("title_font", ""), ("sticker_font", ""),
                      ("body_font", ":ital,wght@0,600;0,700;0,800;1,600")):
        fam = quote_plus(theme[key]) + spec
        if not any(f.split(":")[0] == fam.split(":")[0] for f in fams):
            fams.append(fam)
    return "https://fonts.googleapis.com/css2?" + "&".join(f"family={f}" for f in fams) + "&display=swap"


def build_html(ev: dict, variant: str) -> str:
    tpl = (HERE / "template.html").read_text()
    logo = (ASSETS / "nomad-logo.svg").read_text()
    data = json.dumps(ev).replace("</", "<\\/")
    return (tpl.replace("__FONTS_HREF__", fonts_href(ev["theme"]))
               .replace("__NOMAD_LOGO__", logo)
               .replace("__VARIANT__", variant)
               .replace("__EVENT_JSON__", data))


def render(event_dir: Path, copy_to: Path | None = None) -> list[Path]:
    from playwright.sync_api import sync_playwright

    ev = load_event(event_dir)
    outs = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        try:
            for variant, height in VARIANTS.items():
                html = event_dir / f".{variant}.html"
                html.write_text(build_html(ev, variant))
                page = browser.new_page(viewport={"width": 1080, "height": height})
                page.goto(html.as_uri())
                page.wait_for_load_state("networkidle")
                page.evaluate("window.__ready")
                fit = page.evaluate("window.__fit")
                if fit["overflow"]:
                    raise EventError(f"{variant}: text still overflows at scale {fit['scale']}; shorten the copy")
                out = event_dir / f"{ev['slug']}-{variant}.png"
                page.screenshot(path=str(out))
                html.unlink()
                note = "" if fit["scale"] == 1 else f" (text scaled to {fit['scale']:.0%} to fit)"
                print(f"wrote {out}{note}")
                outs.append(out)
        finally:
            browser.close()
    if copy_to:
        copy_to.mkdir(parents=True, exist_ok=True)
        for o in outs:
            shutil.copy2(o, copy_to / o.name)
            print(f"copied to {copy_to / o.name}")
    return outs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("event_dir", type=Path, help="folder containing event.json")
    ap.add_argument("--copy-to", type=Path, help="also copy the PNGs here (e.g. ~/Downloads)")
    args = ap.parse_args(argv)
    try:
        render(args.event_dir.expanduser().resolve(), args.copy_to.expanduser() if args.copy_to else None)
    except EventError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
