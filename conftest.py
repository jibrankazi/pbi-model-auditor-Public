"""Make the repo root and samples/ importable from the tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for directory in (ROOT, ROOT / "samples"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
