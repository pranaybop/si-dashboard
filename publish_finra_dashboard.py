#!/usr/bin/env python3
"""Patch the rich dashboard from a consistent Supabase snapshot; never regenerate UI.

Auto-expands the FINRA ticker universe on publish (see issue #13). The full
module body is concatenated from publish_finra_dashboard.part1.py and
publish_finra_dashboard.part2.py so tooling can push within size limits;
behavior is identical to a single-file module.
"""
from __future__ import annotations

from pathlib import Path

_dir = Path(__file__).resolve().parent
_SRC = (_dir / "publish_finra_dashboard.part1.py").read_text(encoding="utf-8") + (
    _dir / "publish_finra_dashboard.part2.py"
).read_text(encoding="utf-8")
exec(compile(_SRC, __file__, "exec"), globals())
