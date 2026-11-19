# event-ads

Themed social-media ads for Nomad Karaoke nights (Thanksgiving, Halloween, NYE, ...). Each event is
one folder with an `event.json`; `render.py` turns it into the two sizes we post:

- `<slug>-portrait.png`: 1080x1350 (4:5), the Instagram feed post
- `<slug>-square.png`: 1080x1080, the Facebook post / event cover

Runs on the Mac only. Nothing here deploys to NomadPC.

The whole workflow (brainstorm the copy, render, review, post, boost) is the **`event-ad` skill** in the
workspace (`.agents/skills/event-ad/SKILL.md`). Ask Claude for "a themed ad for <event>" and it runs it.

## Render

```bash
cd tools/event-ads
uv run render.py events/2026-11-26-thanksgiving                    # writes PNGs into the event folder
uv run render.py events/2026-11-26-thanksgiving --copy-to ~/Downloads
```

Needs Google Chrome installed (Playwright drives it; no browser download) and internet access for
Google Fonts and any emoji not yet cached.

## New event

Copy the latest event folder to `events/YYYY-MM-DD-<name>/` (the show date) and edit `event.json`:

| Field | Meaning |
|---|---|
| `slug` | PNG file prefix, e.g. `2026-nye-karaoke` |
| `sticker` | Lines of the tilted white word(s) over the title, e.g. `["GOBBLE", "GOBBLE!"]`, `["SPOOKY!"]` |
| `title` | Big headline word (usually `Karaoke`) |
| `title_indent` | px the title is pushed right so the sticker doesn't hide its first letter (default 60) |
| `venue`, `date`, `time` | Info lines (`date` + `time` stack in pink) |
| `quip` | One-line joke in a pill under the date (optional) |
| `features` | 3-4 rows of `{icon, text}`; `icon` is an emoji character or Noto code (`"🦃"` or `"1f983"`). Optional `overlay` adds a second emoji held up to the first (e.g. the turkey's mic), positioned with `overlay_style` CSS |
| `decorations` | Scattered emoji/images: `{emoji or image, left, top, size, rotate, blur?, opacity?, hide_in?: ["square"]}` in portrait px |
| `theme` | Overrides of `DEFAULT_THEME` in `render.py`: fonts (any Google Font), `title_gradient`, `curtain` (5 colours, top to bottom), `swag` (the curtain's top drape; `null` for none), `accent`, `date_color`, `shadow`, `quip_bg`... `background_image` (a file in the event folder) replaces the curtain |
| `partner_logo` | Defaults to `assets/babylon-kino-logo.png` |
| `caption`, `post`, `event`, `credits` | Not drawn. The caption to post, the posting plan/status, the FB event details, and where every graphic came from |

The layout is a column, so items never overlap. If the copy is too long, the renderer shrinks the
text (down to 60%) and says so; if it still doesn't fit it stops with an error and writes no PNG.

## Graphics rules

**No AI-generated images**, ever (Andrew's rule). Use:

- **Noto Emoji** vector art (Apache 2.0): any emoji by code, fetched to `assets/emoji/` on first use
  and committed. Combine two with `overlay` for something custom.
- **Hand-written SVG/CSS** (the curtain, shapes).
- **Free vector/photo sites with a clear licence** (SVG Repo / Openclipart / Wikimedia Commons public domain or CC0, unDraw).
  Save the file in the event folder and record the source URL and licence in `credits`.
  Avoid "free" stock sites whose licence needs attribution you won't show, and check that it isn't
  AI-generated (many stock sites mix AI art in; skip anything tagged AI).

## Tests

```bash
uv run --no-project --with pytest --with playwright --with pillow pytest -q tests
```

The render tests drive real Chrome and are skipped if it isn't available.

## History

| Event | Notes |
|---|---|
| 2026-10-29 Spooky Karaoke | Made by hand in GIMP before this tool existed (PNG in Andrew's Downloads). Its layout (tilted sticker + big title, venue/date/time, 4 emoji rows, logos) is what the template copies |
| 2026-11-26 Thanksgiving | First event built with this tool |
