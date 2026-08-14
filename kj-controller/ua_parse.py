"""Best-effort User-Agent parsing for singer session provenance.

No third-party dependency — a compact heuristic parser that extracts the
browser, OS, and (where the UA exposes it) device model from a raw User-Agent
string. Used by the KJ Singers list to show a device-details popup so the KJ can
tell a real singer-UI session apart from a duet-partner label / KJ-added entry.

Deliberately conservative: iOS UAs never expose the concrete model ("iPhone"
only), and modern Chromium freezes the version, so ``device`` is a friendly
label, not a guarantee. The raw UA is always kept for the KJ to eyeball.
"""

import re

_ANDROID_MODEL_RE = re.compile(r"Android [\d.]+;\s*(?:[a-z]{2}-[a-z]{2};\s*)?([^;)]+?)(?:\s+Build|;|\))", re.I)
_OS_VERSION_CLEAN_RE = re.compile(r"[_]")


def _os(ua):
    """Return a friendly OS label, e.g. 'iOS 17.4', 'Android 14', 'Windows'."""
    m = re.search(r"iPhone OS ([\d_.]+)", ua) or re.search(r"CPU OS ([\d_.]+)", ua)
    if m:
        return "iOS " + _OS_VERSION_CLEAN_RE.sub(".", m.group(1))
    m = re.search(r"Android ([\d.]+)", ua)
    if m:
        return "Android " + m.group(1)
    if "Windows NT 10.0" in ua:
        return "Windows 10/11"
    if "Windows" in ua:
        return "Windows"
    m = re.search(r"Mac OS X ([\d_.]+)", ua)
    if m:
        return "macOS " + _OS_VERSION_CLEAN_RE.sub(".", m.group(1))
    if "CrOS" in ua:
        return "ChromeOS"
    if "Linux" in ua:
        return "Linux"
    return ""


def _browser(ua):
    """Return a friendly browser label. Order matters (Chrome UAs mention Safari)."""
    # Edge, Samsung, Opera, Firefox must be checked before Chrome/Safari because
    # their UAs also contain "Chrome"/"Safari" tokens.
    for token, label in (
        ("Edg/", "Edge"),
        ("EdgiOS/", "Edge"),
        ("SamsungBrowser/", "Samsung Internet"),
        ("OPR/", "Opera"),
        ("OPiOS/", "Opera"),
        ("FxiOS/", "Firefox"),
        ("Firefox/", "Firefox"),
        ("CriOS/", "Chrome"),
        ("Chrome/", "Chrome"),
    ):
        if token in ua:
            return label
    # Safari last: only a real Safari has "Version/… Safari" without the above.
    if "Safari/" in ua:
        return "Safari"
    return ""


def _device(ua, os_label):
    """Return a friendly device label, extracting an Android model when present."""
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    if "iPod" in ua:
        return "iPod"
    if "Android" in ua:
        m = _ANDROID_MODEL_RE.search(ua)
        if m:
            model = m.group(1).strip()
            # Strip a trailing locale/UA-noise and obvious non-models.
            if model and model.lower() not in ("wv", "mobile", "k"):
                return model
        return "Android device"
    if os_label.startswith("Windows"):
        return "Windows PC"
    if os_label.startswith("macOS"):
        return "Mac"
    if os_label == "ChromeOS":
        return "Chromebook"
    if os_label == "Linux":
        return "Linux PC"
    return ""


def parse_user_agent(ua):
    """Parse a raw UA string into a small dict.

    Returns ``{browser, os, device, is_mobile, raw}``. Empty/None input yields a
    dict with empty fields (and ``raw=''``) so callers never see None.
    """
    ua = (ua or "").strip()
    if not ua:
        return {"browser": "", "os": "", "device": "", "is_mobile": False, "raw": ""}
    os_label = _os(ua)
    return {
        "browser": _browser(ua),
        "os": os_label,
        "device": _device(ua, os_label),
        "is_mobile": ("Mobi" in ua) or ("Android" in ua) or ("iPhone" in ua) or ("iPad" in ua),
        "raw": ua,
    }


def summarize(ua):
    """One-line human summary e.g. 'iPhone · Safari · iOS 17.4'. '' if unknown."""
    p = parse_user_agent(ua)
    parts = [x for x in (p["device"], p["browser"], p["os"]) if x]
    # Dedupe consecutive duplicates (e.g. device == os label) while keeping order.
    seen = []
    for x in parts:
        if x not in seen:
            seen.append(x)
    return " · ".join(seen)
