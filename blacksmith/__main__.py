"""Enable `python -m blacksmith` by delegating to the CLI entrypoint."""

from __future__ import annotations

import sys

from blacksmith.cli import main

if __name__ == "__main__":
    sys.exit(main())
