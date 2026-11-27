import json

import pytest

from nightclips import facebook as F
from nightclips.publish import StepFailed


def test_missing_files_exit_code_2(tmp_path):
    assert F.run_publisher([str(tmp_path / "nope.mp4")], "c", tmp_path) == 2


def test_empty_placeholder_files_are_rejected(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"")  # a Dropbox online-only stub
    assert F.run_publisher([str(f)], "c", tmp_path) == 2


def test_caption_over_facebook_limit_fails_before_opening_anything():
    pub = F.FacebookPublisher(page=None, work_dir=".")
    with pytest.raises(StepFailed, match="too long"):
        pub.run(["x"], "a" * (F.FB_CAPTION_MAX + 1), None, False)


def test_main_reads_request_file(tmp_path, monkeypatch):
    req = tmp_path / "r.json"
    req.write_text(json.dumps({"files": ["a.mp4"], "caption": "hi", "location": "Somewhere"}))
    seen = {}
    monkeypatch.setattr(F, "run_publisher", lambda files, cap, wd, loc, auto: seen.update(
        files=files, cap=cap, loc=loc, auto=auto) or 0)
    assert F.main(["--request", str(req), "--work-dir", str(tmp_path), "--auto-share"]) == 0
    assert seen == {"files": ["a.mp4"], "cap": "hi", "loc": "Somewhere", "auto": True}


class FakeLoc:
    def __init__(self, visible):
        self.visible = visible
        self.first = self

    def count(self):
        return 1 if self.visible else 0

    def is_visible(self):
        return self.visible


class FakePage:
    """Walks through a list of URLs, one per wait_for_timeout; the 'Account' button is visible
    on every page (as it was on Facebook's 2FA chooser, which fooled the old check)."""

    def __init__(self, urls):
        self.urls = list(urls)
        self.url = self.urls.pop(0)
        self.gotos = []

    def wait_for_timeout(self, _ms):
        if self.urls:
            self.url = self.urls.pop(0)

    def goto(self, url, **_kw):
        self.gotos.append((self.url, url))

    def get_by_role(self, *_a, **_kw):
        return FakeLoc(True)

    def locator(self, *_a, **_kw):
        return FakeLoc(True)


@pytest.mark.parametrize("url", [
    "https://www.facebook.com/two_step_verification/two_factor/?encrypted_context=x",
    "https://www.facebook.com/two_step_verification/authentication/?encrypted_context=x",
    "https://www.facebook.com/checkpoint/1501092823525282/",
    "https://www.facebook.com/login/?next=x",
])
def test_auth_flow_pages_never_count_as_logged_in(url):
    pub = F.FacebookPublisher(FakePage([url]), ".")
    assert pub.in_auth_flow()
    assert not pub.is_logged_in()


def test_logged_in_on_the_page_itself():
    assert F.FacebookPublisher(FakePage(["https://www.facebook.com/nomadkaraoke"]), ".").is_logged_in()


def test_ensure_logged_in_waits_through_2fa_without_navigating_away():
    page = FakePage([
        "https://www.facebook.com/login/?next=x",
        "https://www.facebook.com/login/?next=x",  # first is_logged_in check after the initial goto
        "https://www.facebook.com/two_step_verification/authentication/?x",
        "https://www.facebook.com/two_step_verification/two_factor/?x",
        "https://www.facebook.com/two_step_verification/two_factor/?x",
        "https://www.facebook.com/",
    ])
    F.FacebookPublisher(page, ".").ensure_logged_in()
    # Only the initial load and the post-login reload; never a goto while on a 2FA page.
    assert all("two_step" not in frm for frm, _ in page.gotos)
    assert len(page.gotos) == 2


def test_wait_out_auth_flow_times_out():
    pub = F.FacebookPublisher(FakePage(["https://www.facebook.com/checkpoint/1/"]), ".")
    with pytest.raises(StepFailed, match="security check"):
        pub.wait_out_auth_flow(timeout_s=0)
