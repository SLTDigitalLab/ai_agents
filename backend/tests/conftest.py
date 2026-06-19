"""Shared pytest setup for the Ask SLT regression suite.

Makes the backend package importable (`domain`, `core`, ...) regardless of the
directory pytest is launched from, so the suite can be run as either
`pytest` (from backend/) or `pytest backend/tests` (from the repo root).
"""

import os
import sys

# backend/ is the import root for `domain`, `core`, `routers`, etc.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
