import os
import shutil
import subprocess

import pytest

HARNESS = os.path.join(os.path.dirname(__file__), "preview_js_check.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_preview_js_pure_helpers():
    out = subprocess.run(["node", HARNESS], capture_output=True, text=True)
    assert out.returncode == 0, (out.stdout + out.stderr)
    assert "OK" in out.stdout
