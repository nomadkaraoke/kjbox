"""Drive instagram.com (Playwright, headful Chromium) to stage the carousel post.

Flow: open instagram.com -> wait for Andrew to log in if needed -> Create -> Post ->
upload the selected files in order -> "Original" crop -> Next -> Next -> caption ->
**stop before Share** (unless ``--auto-share``). The browser always stays open.

Instagram's web UI changes often, so every step tries several selectors (role +
accessible name first, aria-label / text fallbacks after). On anything unexpected
we screenshot into the night's work dir, print exactly which step failed and leave
the browser where it is so Andrew can finish by hand. Nothing is retried silently.

Run by the review server as ``python -m nightclips.publish --request <json>``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from .config import HOME
from .totp import seconds_left, totp

IG_URL = "https://www.instagram.com/"
PROFILE_DIR = HOME / "ig-profile"
LOGIN_TIMEOUT_S = 15 * 60
LOGIN_URL = "https://www.instagram.com/accounts/login/"
# Set in the nomadkaraoke workspace .envrc (direnv). Only used when the saved
# session in PROFILE_DIR has expired; never logged or echoed.
ENV_USER, ENV_PASS, ENV_2FA = (
    "NOMAD_INSTAGRAM_USERNAME", "NOMAD_INSTAGRAM_PASSWORD", "NOMAD_INSTAGRAM_2FA_SECRET")


def login_credentials() -> tuple[str, str, str] | None:
    """(username, password, totp_secret) from the environment, or None if any is missing."""
    creds = tuple((os.environ.get(k) or "").strip() for k in (ENV_USER, ENV_PASS, ENV_2FA))
    return creds if all(creds) else None
SHORT = 8_000  # ms
LONG = 120_000  # ms (uploads of 20 videos take a while)


def log(msg: str) -> None:
    print(f"[night-clips publish] {msg}", flush=True)


class StepFailed(RuntimeError):
    pass


class Publisher:
    def __init__(self, page, work_dir: Path):
        self.page = page
        self.work_dir = Path(work_dir)

    # -- utilities -------------------------------------------------------------
    def screenshot(self, name: str) -> Path | None:
        path = self.work_dir / f"publish-{time.strftime('%H%M%S')}-{name}.png"
        try:
            self.page.screenshot(path=str(path))
            return path
        except Exception:  # noqa: BLE001
            return None

    def first_visible(self, candidates, timeout_ms: int = SHORT):
        """Return the first locator (of several fallbacks) that becomes visible."""
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

    def dismiss_popups(self) -> None:
        """'Save login info?', 'Turn on notifications', 'Video posts are now reels' etc."""
        p = self.page
        for name in ("Not now", "Not Now", "OK", "Ok"):
            loc = p.get_by_role("button", name=name, exact=True)
            try:
                if loc.count() and loc.first.is_visible():
                    loc.first.click()
                    p.wait_for_timeout(400)
            except Exception:  # noqa: BLE001
                pass

    # -- steps -----------------------------------------------------------------
    def create_button(self):
        p = self.page
        return [
            p.get_by_role("link", name=re.compile(r"^(New post|Create)$", re.I)),
            p.get_by_role("button", name=re.compile(r"^(New post|Create)$", re.I)),
            p.locator("svg[aria-label='New post']").locator("xpath=ancestor::*[self::a or @role='link' or @role='button'][1]"),
            p.locator("a:has(svg[aria-label='New post'])"),
        ]

    def is_logged_in(self) -> bool:
        return self.first_visible(self.create_button(), timeout_ms=1500) is not None

    def ensure_logged_in(self, timeout_s: int = LOGIN_TIMEOUT_S) -> None:
        self.page.goto(IG_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)
        self.dismiss_popups()
        if self.is_logged_in():
            log("Logged in to Instagram.")
            return
        creds = login_credentials()
        if creds:
            try:
                if self.auto_login(*creds):
                    log("Logged in automatically (saved session had expired).")
                    return
                log("Automatic login didn't finish (Instagram may want an extra check).")
            except Exception as e:  # never let a selector change block the manual path
                log(f"Automatic login failed at an unexpected step ({type(e).__name__}).")
        else:
            log(f"(Set {ENV_USER} / {ENV_PASS} / {ENV_2FA} to log in automatically.)")
        log("NOT logged in. Please log in to @nomadkaraoke in the Chromium window now")
        log(f"(the session is saved in {PROFILE_DIR}, so this is a one-time step).")
        log(f"Waiting up to {timeout_s // 60} minutes...")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(3000)
            self.dismiss_popups()
            if self.is_logged_in():
                log("Login detected, continuing.")
                if "instagram.com" not in self.page.url or "/accounts/" in self.page.url:
                    self.page.goto(IG_URL, wait_until="domcontentloaded")
                    self.page.wait_for_timeout(2000)
                    self.dismiss_popups()
                return
        raise StepFailed("login: timed out waiting for you to log in")

    def code_inputs(self):
        p = self.page
        return [
            p.locator("input[name='verificationCode']"),
            p.locator("input[autocomplete='one-time-code']"),
            p.get_by_label(re.compile(r"^code$|security code|confirmation code|6-digit", re.I)),
            # Meta-style "Go to your authentication app" page (accounts/login/two_step_verification).
            p.locator("input[type='text'], input[type='tel'], input[inputmode='numeric'], input:not([type])")
             .filter(has_not=p.locator("[name='username'], [type='password']"))
            if "two_step" in p.url or "two_factor" in p.url else p.locator("input[name='__none__']"),
        ]

    def auto_login(self, username: str, password: str, secret: str) -> bool:
        """Username + password + TOTP login. True once the home feed shows.

        Returns False (leaving the page as-is for a manual finish) on anything
        unexpected: a suspicious-login checkpoint, captcha, emailed code, etc.
        """
        p = self.page
        log("Saved session expired; logging in automatically...")
        p.goto(LOGIN_URL, wait_until="domcontentloaded")
        p.wait_for_timeout(2500)
        self.dismiss_popups()
        user = self.first_visible([p.locator("input[name='username']"),
                                   p.get_by_label(re.compile(r"username|email", re.I))])
        pw = self.first_visible([p.locator("input[name='password']"),
                                 p.get_by_label(re.compile(r"^password", re.I))])
        if user is None or pw is None:
            return False
        user.fill(username)
        pw.fill(password)
        pw.press("Enter")
        # Either the feed (no 2FA), the code form, or something we don't handle.
        code_box = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            p.wait_for_timeout(1500)
            if self.is_logged_in():
                break
            code_box = self.first_visible(self.code_inputs(), timeout_ms=500)
            if code_box is not None:
                break
        if code_box is not None:
            if seconds_left() < 4:  # don't submit a code that's about to roll over
                p.wait_for_timeout(int(seconds_left() * 1000) + 500)
            code_box.fill(totp(secret))
            # Newer Meta 2FA page: "Continue" button (keeps "Trust this device" ticked).
            if not self.click_any("2fa-submit", [
                p.get_by_role("button", name=re.compile(r"^(continue|confirm|submit)$", re.I)),
            ], timeout_ms=2000, required=False):
                code_box.press("Enter")
            log("Entered the 2FA code.")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not self.is_logged_in():
                p.wait_for_timeout(1500)
                # "Save your login info?" -> save, so the session lasts.
                self.click_any("save-login", [
                    p.get_by_role("button", name=re.compile(r"^save( info)?$", re.I)),
                ], timeout_ms=500, required=False)
                self.dismiss_popups()
        if not self.is_logged_in():
            return False
        if "/accounts/" in p.url:
            p.goto(IG_URL, wait_until="domcontentloaded")
            p.wait_for_timeout(2000)
            self.dismiss_popups()
        return True

    def open_new_post(self) -> None:
        p = self.page
        self.click_any("create", self.create_button())
        p.wait_for_timeout(800)
        # Newer UI shows a submenu (Post / Live video / Ad / AI). Older UI opens the dialog directly.
        self.click_any(
            "create > Post",
            [
                p.get_by_role("link", name=re.compile(r"^Post$", re.I)),
                p.get_by_role("menuitem", name=re.compile(r"^Post$", re.I)),
                p.get_by_role("button", name=re.compile(r"^Post$", re.I)),
                p.locator("svg[aria-label='Post']").locator("xpath=ancestor::*[self::a or @role='link' or @role='button' or @role='menuitem'][1]"),
            ],
            timeout_ms=3000,
            required=False,
        )
        dlg = self.first_visible(
            [p.get_by_role("dialog").filter(has_text=re.compile(r"Create new post|Drag photos and videos", re.I))],
            timeout_ms=SHORT,
        )
        if dlg is None:
            raise StepFailed("create: the 'Create new post' dialog didn't open")

    def upload(self, files: list[str]) -> None:
        p = self.page
        inputs = p.locator("div[role='dialog'] input[type='file'], form input[type='file'][multiple], input[type='file'][accept*='video']")
        if inputs.count():
            inputs.first.set_input_files(files)
        else:
            btn = self.first_visible([p.get_by_role("button", name=re.compile(r"Select from computer", re.I))])
            if btn is None:
                raise StepFailed("upload: no file input and no 'Select from computer' button")
            with p.expect_file_chooser(timeout=SHORT) as fc_info:
                btn.click()
            fc = fc_info.value
            if not fc.is_multiple() and len(files) > 1:
                raise StepFailed("upload: Instagram's file picker doesn't accept multiple files")
            fc.set_files(files)
        log(f"Uploading {len(files)} file(s)...")
        # "Video posts are now shared as reels" info popup, then the crop screen.
        p.wait_for_timeout(1500)
        self.dismiss_popups()
        crop_ready = self.first_visible(
            [p.get_by_role("button", name=re.compile(r"^Next$", re.I)),
             p.get_by_role("dialog").get_by_text("Next", exact=True)],
            timeout_ms=LONG,
        )
        if crop_ready is None:
            raise StepFailed("upload: the crop screen ('Next') never appeared")

    def choose_original_crop(self) -> None:
        p = self.page
        opened = self.click_any(
            "crop",
            [p.get_by_role("button", name=re.compile(r"Select crop", re.I)),
             p.locator("svg[aria-label='Select crop']").locator("xpath=ancestor::*[@role='button' or self::button][1]")],
            timeout_ms=4000,
            required=False,
        )
        if not opened:
            log("WARNING: couldn't find the crop selector; leaving Instagram's default crop. Check it before sharing.")
            return
        picked = self.click_any(
            "crop > Original",
            [p.get_by_role("button", name=re.compile(r"^Original", re.I)),
             p.get_by_text("Original", exact=True)],
            timeout_ms=4000,
            required=False,
        )
        log("Crop set to Original." if picked else "WARNING: no 'Original' crop option; check the crop by hand.")
        # Close the crop popover again so it doesn't eat the Next click.
        if picked:
            p.wait_for_timeout(300)

    def next(self, step: str) -> None:
        p = self.page
        self.click_any(
            step,
            [p.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Next$", re.I)),
             p.get_by_role("button", name=re.compile(r"^Next$", re.I)),
             p.get_by_role("dialog").get_by_text("Next", exact=True)],
            timeout_ms=SHORT,
        )
        p.wait_for_timeout(1200)

    def fill_caption(self, caption: str) -> None:
        p = self.page
        box = self.first_visible(
            [p.get_by_role("textbox", name=re.compile(r"Write a caption", re.I)),
             p.locator("div[aria-label^='Write a caption'][contenteditable='true']"),
             p.locator("div[role='dialog'] div[contenteditable='true']")],
            timeout_ms=SHORT,
        )
        if box is None:
            raise StepFailed("caption: couldn't find the 'Write a caption...' box")
        box.click()
        lines = caption.split("\n")
        for i, line in enumerate(lines):
            if line:
                p.keyboard.insert_text(line)
            if i < len(lines) - 1:
                p.keyboard.press("Shift+Enter")
        p.wait_for_timeout(500)
        try:
            got = box.inner_text()
            if len(got.strip()) < len(caption.strip()) * 0.9:
                log("WARNING: the caption box looks shorter than expected (Instagram limit is 2,200 chars). Check it.")
        except Exception:  # noqa: BLE001
            pass
        log("Caption filled.")

    def share(self) -> None:
        p = self.page
        self.click_any(
            "share",
            [p.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Share$", re.I)),
             p.get_by_role("dialog").get_by_text("Share", exact=True)],
        )
        log("Share clicked; waiting for Instagram to finish processing (can take minutes)...")
        done = self.first_visible(
            [p.get_by_text(re.compile(r"(Your post has been shared|Post shared|Reel shared)", re.I))],
            timeout_ms=10 * 60_000,
        )
        if done is None:
            raise StepFailed("share: didn't see the 'shared' confirmation; check the window")
        log("Instagram says the post was shared. Click 'Mark as posted' on the review page.")

    def run(self, files: list[str], caption: str, auto_share: bool) -> None:
        self.ensure_logged_in()
        self.open_new_post()
        self.upload(files)
        self.choose_original_crop()
        self.next("crop > Next")
        self.next("edit > Next")
        self.fill_caption(caption)
        if auto_share:
            self.share()
        else:
            log("STOPPED before Share. Check the order, crop and caption, then click Share yourself.")
            log("Afterwards click 'Mark as posted' on the review page.")


def run_publisher(files: list[str], caption: str, work_dir: Path, auto_share: bool = False,
                  profile_dir: Path = PROFILE_DIR) -> int:
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
                str(profile_dir), headless=False, no_viewport=True, args=["--start-maximized"]
            )
        except PWError as e:
            if "Executable doesn't exist" in str(e):
                log("Chromium isn't installed for Playwright: run `uv run playwright install chromium`.")
                return 2
            raise
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        pub = Publisher(page, Path(work_dir))
        try:
            pub.run(files, caption, auto_share)
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
        except Exception:  # noqa: BLE001 — window closed / Ctrl-C
            pass
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightclips.publish")
    ap.add_argument("--request", required=True, help="publish_request.json written by the review server")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--auto-share", action="store_true")
    args = ap.parse_args(argv)
    req = json.loads(Path(args.request).read_text())
    return run_publisher(req["files"], req["caption"], Path(args.work_dir), args.auto_share)


if __name__ == "__main__":
    sys.exit(main())
