"""Pytest configuration shared by every test module.

Mirrors the sys.path trick in main.py so `from src...` imports work when
running pytest directly from the repo root, without needing an editable
install.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
