import shutil
import subprocess
from pathlib import Path

import pytest

TEST_FILE = Path(__file__).resolve().parents[2] / "dashboard" / "tests" / "maint-events.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_dashboard_maintenance_events():
    result = subprocess.run(["node", "--test", str(TEST_FILE)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
