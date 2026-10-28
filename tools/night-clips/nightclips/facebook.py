"""Drive facebook.com (Playwright, headful Chromium) to stage the same post on the Nomad Karaoke Page.

Flow: open the Page -> wait for Andrew to log in if needed -> "Switch Now" into the Page if
Facebook asks -> Photo/video -> upload the files in order -> caption -> optional location
(Check in) -> **stop before Next/Post** (unless ``--auto-share``). The browser stays open.

Facebook's web UI changes often, so each step tries several selectors, screenshots into the
night's work dir on anything unexpected, and leaves the browser where it is so Andrew can
finish by hand. Facebook login is always manual (the saved session in PROFILE_DIR lasts).

Run by the review server as ``python -m nightclips.facebook --request <json>``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from .config import HOME
from .publish import LONG, SHORT, StepFailed, log as _log

FB_PAGE_URL = "https://www.facebook.com/nomadkaraoke"
PROFILE_DIR = HOME / "fb-profile"
LOGIN_TIMEOUT_S = 15 * 60
FB_CAPTION_MAX = 63_206


def log(msg: str) -> None:
    _log(f"[facebook] {msg}")


class FacebookPublisher:
    def __init__(self, page, work_dir: Path):
        self.page = page
        self.work_dir = Path(work_dir)

    # -- utilities (same shape as publish.Publisher) ---------------------------
    def screenshot(self, name: str) -> Path | None:
        path = self.work_dir / f"facebook-{time.strftime('%H%M%S')}-{name}.png"
        try:
            self.page.screenshot(path=str(path))
            return path
        except Exception:  # noqa: BLE001
            return None

    def first_visible(self, candidates, timeout_ms: int = SHORT):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for loc in candidates:
                try:
                    if loc.count() and loc.first.is_visible():
                        return loc.first
                except Exception:  # noqa: BLE001 — page mid-navigation
                    pass
            self.page.wait_for_timeout(250)
        return None

    def click_any(self, step: str, candidates, timeout_ms: int = SHORT, required: bool = True) -> bool:
        loc = self.first_visible(candidates, timeout_ms)
        if loc is None:
            if required:
                raise StepFailed(f"{step}: couldn't find the button/link")
            return False
        loc.click()
        return True

    # -- steps -----------------------------------------------------------------
    def composer_button(self):
        p = self.page
        return [
            p.get_by_role("button", name=re.compile(r"^Photo/video$", re.I)),
            p.locator("[aria-label='Photo/video'][role='button']"),
        ]

    def is_logged_in(self) -> bool:
        p = self.page
        # The top-bar account switcher / Page header only exist when logged in.
        return self.first_visible([
            p.get_by_role("button", name=re.compile(r"^(Account|Your profile)", re.I)),
            p.locator("[aria-label='Your profile'], [aria-label='Account']"),
            *self.composer_button(),
        ], timeout_ms=2500) is not None

    def ensure_logged_in(self) -> None:
        p = self.page
        p.goto(FB_PAGE_URL, wait_until="domcontentloaded")
        p.wait_for_timeout(3000)
        if self.is_logged_in():
            log("Logged in to Facebook.")
            return
        log("NOT logged in. Please log in to Facebook in the Chromium window now")
        log(f"(the session is saved in {PROFILE_DIR}, so this is a one-time step).")
        deadline = time.monotonic() + LOGIN_TIMEOUT_S
        while time.monotonic() < deadline:
            p.wait_for_timeout(3000)
            if self.is_logged_in():
                log("Login detected, continuing.")
                p.goto(FB_PAGE_URL, wait_until="domcontentloaded")
                p.wait_for_timeout(3000)
                return
        raise StepFailed("login: timed out waiting for you to log in")

    def switch_into_page(self) -> None:
        """When you manage the Page from a personal profile, Facebook shows 'Switch Now'
        (and greys out Insights / Settings). Posting as the Page needs that switch."""
        p = self.page
        sw = self.first_visible([p.get_by_role("button", name=re.compile(r"^Switch now$", re.I)),
                                 p.get_by_text(re.compile(r"^Switch now$", re.I))], timeout_ms=3000)
        if sw is None:
            return
        log("Switching into the Page...")
        sw.click()
        p.wait_for_timeout(4000)

    def open_composer_and_upload(self, files: list[str]) -> None:
        """Clicking Photo/video opens the OS file picker straight away, so the picker has to be
        caught on that first click (a second click finds nothing). Falls back to a file input
        or an 'Add photos/videos' button if Facebook's layout opens the composer dialog first."""
        p = self.page
        # A security check / checkpoint can sit in front of the Page; give Andrew time to clear it.
        if self.first_visible(self.composer_button(), timeout_ms=5000) is None:
            log("Photo/video button not showing yet. If Facebook is asking for a security check, "
                "finish it in the window; waiting up to 3 minutes...")
            p.goto(FB_PAGE_URL, wait_until="domcontentloaded")
            self.switch_into_page()
        btn = self.first_visible(self.composer_button(), timeout_ms=3 * 60_000)
        if btn is None:
            raise StepFailed("composer: couldn't find the Photo/video button")
        try:
            with p.expect_file_chooser(timeout=SHORT) as fc_info:
                btn.click()
            fc_info.value.set_files(files)
        except Exception:  # noqa: BLE001 — no picker: the composer dialog opened instead
            inputs = p.locator("div[role='dialog'] input[type='file']")
            if inputs.count():
                inputs.first.set_input_files(files)
            else:
                with p.expect_file_chooser(timeout=SHORT) as fc_info:
                    self.click_any("add media", [
                        p.get_by_role("dialog").get_by_role("button", name=re.compile(r"Photo/video", re.I)),
                        p.get_by_role("dialog").get_by_text("Add photos/videos", exact=False),
                    ])
                fc_info.value.set_files(files)
        log(f"Uploading {len(files)} file(s)...")
        ready = self.first_visible(
            [p.get_by_role("dialog").get_by_role("button", name=re.compile(r"^(Next|Post)$", re.I)),
             p.get_by_role("dialog").get_by_role("button", name=re.compile(r"Edit all", re.I))],
            timeout_ms=LONG,
        )
        if ready is None:
            raise StepFailed("upload: the composer never showed the uploaded media")
        p.wait_for_timeout(1500)

    def fill_caption(self, caption: str) -> None:
        p = self.page
        box = self.first_visible(
            [p.get_by_role("dialog").get_by_role("textbox", name=re.compile(r"What's on your mind|Create a public post|Write something", re.I)),
             p.locator("div[role='dialog'] div[role='textbox'][contenteditable='true']")],
            timeout_ms=SHORT,
        )
        if box is None:
            raise StepFailed("caption: couldn't find the post text box")
        box.click()
        lines = caption.split("\n")
        for i, line in enumerate(lines):
            if line:
                p.keyboard.insert_text(line)
            if i < len(lines) - 1:
                p.keyboard.press("Shift+Enter")
        p.wait_for_timeout(500)
        log("Caption filled.")

    def add_location(self, query: str) -> None:
        """Best-effort 'Check in'. Never fatal: the location search is the part Facebook changes most."""
        p = self.page
        if not self.click_any("check-in", [
            p.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Check in$", re.I)),
            p.locator("div[role='dialog'] [aria-label='Check in']"),
        ], timeout_ms=3000, required=False):
            log("WARNING: couldn't find 'Check in'; add the location by hand if you want one.")
            return
        search = self.first_visible([p.get_by_role("textbox", name=re.compile(r"Where are you|Search for a location", re.I)),
                                     p.locator("div[role='dialog'] input[type='search'], div[role='dialog'] input[type='text']")],
                                    timeout_ms=4000)
        if search is None:
            log("WARNING: no location search box; add the location by hand.")
            return
        search.fill(query)
        p.wait_for_timeout(2500)
        first = self.first_visible([p.get_by_role("option").first, p.locator("div[role='dialog'] [role='listitem']").first],
                                   timeout_ms=4000)
        if first is None:
            log(f"WARNING: no location results for '{query}'; add it by hand.")
            return
        first.click()
        p.wait_for_timeout(1000)
        log(f"Location set from search '{query}' (check it is the right place).")

    def next_and_post(self, auto_share: bool) -> None:
        p = self.page
        dlg = p.get_by_role("dialog")
        self.click_any("next", [dlg.get_by_role("button", name=re.compile(r"^Next$", re.I))], timeout_ms=SHORT, required=False)
        p.wait_for_timeout(1500)
        if not auto_share:
            log("STOPPED before Post. Check the media, caption and audience, then click Post yourself.")
            return
        self.click_any("post", [dlg.get_by_role("button", name=re.compile(r"^Post$", re.I))], timeout_ms=SHORT)
        log("Post clicked.")

    def run(self, files: list[str], caption: str, location: str | None, auto_share: bool) -> None:
        if len(caption) > FB_CAPTION_MAX:
            raise StepFailed("caption: too long for Facebook")
        self.ensure_logged_in()
        self.switch_into_page()
        self.open_composer_and_upload(files)
        self.fill_caption(caption)
        if location:
            self.add_location(location)
        self.next_and_post(auto_share)


def run_publisher(files: list[str], caption: str, work_dir: Path, location: str | None = None,
                  auto_share: bool = False, profile_dir: Path = PROFILE_DIR) -> int:
    missing = [f for f in files if not Path(f).is_file() or Path(f).stat().st_size == 0]
    if missing:
        log("These files aren't downloaded: " + ", ".join(missing))
        return 2
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Playwright isn't installed: run `uv sync` in tools/night-clips.")
        return 2
    profile_dir.mkdir(parents=True, exist_ok=True)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    rc = 0
    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(profile_dir), headless=False, no_viewport=True, args=["--start-maximized"])
        except PWError as e:
            if "Executable doesn't exist" in str(e):
                log("Chromium isn't installed for Playwright: run `uv run playwright install chromium`.")
                return 2
            raise
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        pub = FacebookPublisher(page, Path(work_dir))
        try:
            pub.run(files, caption, location, auto_share)
        except StepFailed as e:
            rc = 1
            shot = pub.screenshot("failed")
            log(f"FAILED at step -> {e}")
            log(f"Screenshot: {shot}" if shot else "(screenshot failed)")
            log("The browser is left exactly where it stopped; finish the post by hand.")
        except PWError as e:
            rc = 1
            shot = pub.screenshot("error")
            log(f"FAILED (Playwright error): {e}")
            log(f"Screenshot: {shot}" if shot else "(screenshot failed)")
        log("Leaving the browser open. Close the Chromium window when you're done.")
        try:
            ctx.wait_for_event("close", timeout=0)
        except Exception:  # noqa: BLE001
            pass
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightclips.facebook")
    ap.add_argument("--request", required=True, help="request json: {files, caption, location?}")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--auto-share", action="store_true")
    args = ap.parse_args(argv)
    req = json.loads(Path(args.request).read_text())
    return run_publisher(req["files"], req["caption"], Path(args.work_dir), req.get("location"), args.auto_share)


if __name__ == "__main__":
    sys.exit(main())
