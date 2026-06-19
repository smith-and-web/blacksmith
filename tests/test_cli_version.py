"""WU-CLI-VERSION: ``blacksmith --version`` prints the version and exits 0."""

from __future__ import annotations

import pytest

from blacksmith import __version__
from blacksmith.cli import main


def test_version_flag_exits_zero_and_prints_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out
