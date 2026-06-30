import os
import shutil
import subprocess

import pytest

HARNESS = os.path.join(os.path.dirname(__file__), "cdg_render_check.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cdg_renderer_pixels():
    out = subprocess.run(["node", HARNESS], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, (out.stdout + out.stderr)
    assert "OK" in out.stdout
