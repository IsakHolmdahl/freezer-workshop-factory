#!/usr/bin/env python3
"""Remove everything ./setup.py created so you can start fresh. See: ./cleanup.py --help

Runs scripts/cleanup.py with the .venv Python (needed for the menu and the config files).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
venv_python = ROOT / ".venv" / "bin" / "python"
if not venv_python.exists():
    try:
        import yaml  # noqa: F401  (scripts/cleanup.py needs PyYAML and python-dotenv)
        import dotenv  # noqa: F401
    except ImportError:
        sys.exit("✘ the Python environment is missing: run ./setup.py first, "
                 "or: python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt")
python = str(venv_python) if venv_python.exists() else sys.executable
os.execv(python, [python, str(ROOT / "scripts" / "cleanup.py"), *sys.argv[1:]])
