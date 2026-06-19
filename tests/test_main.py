"""`python -m blacksmith` entrypoint contract (WU-CLI-MAIN)."""

from __future__ import annotations

import subprocess
import sys


def test_module_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "blacksmith", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "blacksmith" in result.stdout
