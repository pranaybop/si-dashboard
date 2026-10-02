#!/usr/bin/env python3
"""Patch the rich dashboard from a consistent Supabase snapshot; never regenerate UI.

The existing curated ticker universe and basket definitions remain the template.
Float ratios are retained only for dates with a published denominator; new dates
have no SI% until float is refreshed. Prices are remapped by settlement date.

On every publish, symbols newly present in the latest FINRA settlement (and
passing eligibility gates) are auto-added into the template with empty pct
series until CapIQ/float refresh. See is_eligible_new_symbol / select_new_tickers.

Implementation note: body is gzip+base64 in sibling fragment files
(publish_finra_dashboard.b64a / .b64b) so automation can push within API size
limits; behavior matches the uncompressed source (sha256 of body documented in PR).
"""
from __future__ import annotations

import base64
import gzip
from pathlib import Path

_dir = Path(__file__).resolve().parent
_b64 = (_dir / "publish_finra_dashboard.b64a").read_text() + (
    _dir / "publish_finra_dashboard.b64b"
).read_text()
_SRC = gzip.decompress(base64.b64decode(_b64)).decode("utf-8")
exec(compile(_SRC, __file__, "exec"), globals())
