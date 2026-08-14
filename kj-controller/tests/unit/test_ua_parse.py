"""Unit tests for ua_parse — best-effort User-Agent parsing."""

from ua_parse import parse_user_agent, summarize

IOS_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
ANDROID_CHROME = (
    "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)
ANDROID_SAMSUNG = (
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36"
)
WIN_EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
)
MAC_FIREFOX = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0"
)
IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
)


class TestParse:
    def test_ios_safari(self):
        p = parse_user_agent(IOS_SAFARI)
        assert p["device"] == "iPhone"
        assert p["browser"] == "Safari"
        assert p["os"] == "iOS 17.4"
        assert p["is_mobile"] is True

    def test_android_model_extracted(self):
        p = parse_user_agent(ANDROID_CHROME)
        assert p["device"] == "SM-S911B"
        assert p["browser"] == "Chrome"
        assert p["os"] == "Android 14"
        assert p["is_mobile"] is True

    def test_samsung_browser_before_chrome(self):
        # UA contains both "SamsungBrowser" and "Chrome" — Samsung must win.
        p = parse_user_agent(ANDROID_SAMSUNG)
        assert p["browser"] == "Samsung Internet"
        assert p["device"] == "SM-G991B"

    def test_edge_before_chrome(self):
        p = parse_user_agent(WIN_EDGE)
        assert p["browser"] == "Edge"
        assert p["os"] == "Windows 10/11"
        assert p["is_mobile"] is False

    def test_edge_for_android(self):
        # EdgA/ (Edge on Android) also contains Chrome/ — Edge must win.
        ua = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 EdgA/120.0.0.0")
        p = parse_user_agent(ua)
        assert p["browser"] == "Edge"
        assert p["device"] == "Pixel 7"
        assert p["is_mobile"] is True

    def test_mac_firefox_keeps_full_version(self):
        p = parse_user_agent(MAC_FIREFOX)
        assert p["browser"] == "Firefox"
        assert p["os"] == "macOS 10.15"
        assert p["device"] == "Mac"

    def test_ipad(self):
        p = parse_user_agent(IPAD)
        assert p["device"] == "iPad"
        assert p["os"] == "iOS 16.5"

    def test_empty_and_none(self):
        for ua in ("", None, "   "):
            p = parse_user_agent(ua)
            assert p == {"browser": "", "os": "", "device": "", "is_mobile": False, "raw": ""}

    def test_raw_preserved(self):
        assert parse_user_agent(IOS_SAFARI)["raw"] == IOS_SAFARI

    def test_junk_does_not_crash(self):
        p = parse_user_agent("not a real user agent !!! 123")
        assert p["raw"] == "not a real user agent !!! 123"

    def test_embedded_webkit_not_tagged_safari(self):
        # An in-app WebView exposes a bare Safari/ token but no Version/ — must
        # NOT be classified as Safari.
        ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1")
        assert parse_user_agent(ua)["browser"] == ""


class TestSummarize:
    def test_summary_dedupes_and_orders(self):
        assert summarize(IOS_SAFARI) == "iPhone · Safari · iOS 17.4"

    def test_summary_empty(self):
        assert summarize("") == ""
