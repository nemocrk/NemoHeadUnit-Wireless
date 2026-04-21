"""
conftest.py — shared pytest fixtures for v2 module tests.

Sets up sys.path so all v2 modules are importable without installation.
"""

import sys
from pathlib import Path

# Repo root
_ROOT    = Path(__file__).parents[2]
_V2      = _ROOT / "v2"
_MODULES = _V2 / "modules"

for p in [str(_V2), str(_MODULES)]:
    if p not in sys.path:
        sys.path.insert(0, p)
