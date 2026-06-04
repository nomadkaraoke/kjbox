"""Run the REAL static/text_normalize.js under node over a shared corpus and
assert byte-identical output with the Python normalizer."""
import json
import os
import shutil
import subprocess
import pytest

import text_normalize as tn

HERE = os.path.dirname(__file__)
JS_FILE = os.path.abspath(os.path.join(HERE, "..", "..", "static", "text_normalize.js"))

CASES = [
    "Simon & Garfunkel", "Don't Stop Believin'", "Beyoncé", "MØ",
    "Straßenbande", "Twenty One Pilots", "Thirty Seconds To Mars",
    "Blink 182", "Two Princes", "Rocky IV", "Stay feat. Justin Bieber",
    "Florence + The Machine", "Song Pt 2", "A vs B", "I Will Survive",
    "187 Straßenbande - Millionär", "The Sound of Silence", "", "Queen",
]


def _maps():
    return {
        "latinSpecialMap": tn.LATIN_SPECIAL_MAP,
        "abbrevMap": tn.ABBREV_MAP,
        "numberWords": tn.NUMBER_WORDS,
        "romanMap": tn.ROMAN_NUMERALS,
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_matches_python():
    script = (
        "const tn=require(process.argv[1]);"
        "const maps=JSON.parse(process.argv[2]);"
        "const cases=JSON.parse(process.argv[3]);"
        "const n=tn.makeNormalizer(maps);"
        "console.log(JSON.stringify(cases.map(c=>n.normalize(c))));"
    )
    out = subprocess.check_output(
        ["node", "-e", script, JS_FILE, json.dumps(_maps()), json.dumps(CASES)],
        text=True,
    )
    js_results = json.loads(out)
    py_results = [tn.normalize(c) for c in CASES]
    assert js_results == py_results
