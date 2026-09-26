#!/usr/bin/env python3
"""Fail if kj-controller/static-sing/*.js uses a t()/tn() key that is
missing from static-sing/messages/en.json, or the template uses a data-i18n
key that is missing. Dynamic keys (template literals) are checked by prefix."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "kj-controller"
# App modules only (i18n.js itself has t("a.b")-style doc examples).
JS = "\n".join((ROOT / "static-sing" / f).read_text(encoding="utf-8") for f in ("sing.js", "make.js"))
HTML = (ROOT / "templates" / "sing.html").read_text(encoding="utf-8")
EN = json.loads((ROOT / "static-sing" / "messages" / "en.json").read_text(encoding="utf-8"))


def flatten(obj, prefix=""):
    out = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


flat = flatten(EN)
used = set(re.findall(r'\btn?\("([a-zA-Z0-9_.]+)"', JS))
used |= set(re.findall(r'data-i18n(?:-[a-z-]+)?="([a-zA-Z0-9_.]+)"', HTML))
dynamic = set(re.findall(r'\btn?\(`([^`$]+)\$\{', JS))

missing = sorted(
    k for k in used
    if k not in flat and not any(f.startswith(k + ".") for f in flat)
)
bad_dynamic = sorted(
    p for p in dynamic if not any(f.startswith(p) for f in flat)
)
if missing or bad_dynamic:
    for k in missing:
        print(f"missing key: {k}")
    for p in bad_dynamic:
        print(f"no keys under dynamic prefix: {p}")
    sys.exit(1)
print(f"ok: {len(used)} static keys + {len(dynamic)} dynamic prefixes resolve against en.json")
