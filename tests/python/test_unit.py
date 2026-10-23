"""Collects the unit suites (same layout as the studios): python -m pytest tests/python/test_unit.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "launcher"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher_suite import *  # noqa: E402,F401,F403
