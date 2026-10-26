"""Owned SDK child entry point; no inherited terminal process group."""

import os
import sys

if __name__ == "__main__":
    os.setsid()
    os.execv(sys.argv[1], sys.argv[1:])
