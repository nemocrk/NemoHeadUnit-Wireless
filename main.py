#!/usr/bin/env python3
"""
NemoHeadUnit-Wireless Main Entry Point.

Delegates execution to backend/main.py.
"""
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if __name__ == "__main__":
    import runpy
    backend_main = BACKEND_DIR / "main.py"
    runpy.run_path(str(backend_main), run_name="__main__")
