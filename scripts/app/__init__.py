"""Make project ``app`` imports available to directly executed scripts.

When Python runs ``python /app/scripts/<name>.py``, ``/app/scripts`` becomes the
first import root. This lightweight namespace bridge points ``app.*`` imports at
the real ``/app/app`` package without requiring callers to set PYTHONPATH.
"""

from __future__ import annotations

from pathlib import Path


_REAL_APP = Path(__file__).resolve().parents[2] / "app"
__path__ = [str(_REAL_APP)]
