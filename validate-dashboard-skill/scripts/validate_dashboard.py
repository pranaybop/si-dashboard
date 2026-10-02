#!/usr/bin/env python3
"""
validate_dashboard.py - SI Dashboard Health Checker
----------------------------------------------------
Run after any edit to si_dashboard.html to catch silent truncation,
missing functions, broken JSON blobs, or mismatched HTML structure.

Usage:
    python validate_dashboard.py                         # checks si_dashboard.html in same folder
    python validate_dashboard.py path/to/dashboard.html  # checks a specific file

Exit codes: 0 = all clear, 1 = errors found

Checks:
  1  File structure      DOCTYPE / </html> tail / <body> balance
  2  Script tag balance  Mismatched <script>...</script> pairs
  3  JS brace balance    Unclosed braces per script block (catches truncation)
  4  Required JS symbols Every named function / variable present
  5  Function sentinels  Key functions contain known end-of-body markers
  6  Near-EOF risk       Warns if critical code sits in last 10% of file
  7  DOM element IDs     All IDs the JS references exist in HTML
  8  INSIGHTS_DATA JSON  Fully parseable + theme count + required IDs
  9  Data blocks         RAW / SECTOR_DATA / INSIGHTS_DATA present
  10 UI thresholds       card / badge / alert thresholds all agree
  11 File size bounds    Flags accidental deletions or double-embeds
"""

import sys
import re
import json
import os

# ── Configuration ─────────────────────────────────────────────────────────────

REQUIRED_JS = [
    ("switchTab",         "function switchTab("),
    ("renderSector",      "window.renderSector"),
    ("renderThemes",      "window.renderThemes = function"),
    ("renderThemeDetail", "window.renderThemeDetail = function"),
    ("renderRising",      "window.renderRising"),
    ("themeRange",        "function themeRange("),
    ("rebase100",         "function rebase100("),
    ("getPctSeries",      "function getPctSeries("),
    ("getRawSI",          "function getRawSI("),
    ("_origSwitchTab2",   "var _origSwitchTab2=window.switchTab"),
    ("fmtSI2",            "function fmtSI2("),
    ("zBadge",            "function zBadge("),
]

# Sentinels: something unique near the END of each key function body.
# If truncated mid-function this marker will be absent.
FUNCTION_SENTINELS = [
    ("renderThemeDetail",
     'document.getElementById("th-detail").style.display="block"'),
    ("renderThemes",
     'document.getElementById("th-alerts").innerHTML=alertsH'),
    ("switchTab wrapper",
     'if(name==="themes"){setTimeout(window.renderThemes'),
]

# These symbols in the last 10% of the file are high-risk for Edit-tool truncation.
NEAR_EOF_RISK_SYMBOLS = [
    "window.renderThemeDetail = function",
    "window.renderThemes = function",
    "var _origSwitchTab2=window.switchTab",
]

REQUIRED_IDS = [
    "th-period", "th-view", "th-yaxis", "th-grid", "th-alerts",
    "th-chart", "th-tbody", "th-detail", "th-dtitle", "th-dsub",
    "st-overlay",
]

REQUIRED_THEMES = [
    "smcap_biotech", "fintech_bnpl", "ai_cloud", "bdc",
    "crit_metals", "enterprise_sw", "cloud_saas", "semis",
    "crypto", "clean_energy",
]
EXPECTED_THEME_COUNT = 40

THRESHOLD_CHECKS = [
    ("card border -0.4",    '-0.4?"th-cold"'),
    ("badge neutral -0.4",  'heat>=-0.4?\'<span class="th-badge th-badge-neutral"'),
    ("alert covering -0.4", 'return(t.heat||0)<=-0.4'),
    ("covering alert label", ("\U0001f7e2 Covering (long signal):", "\U0001f7e2 Covering:")),
]

DATA_MARKERS = [
    ("const RAW",     "const RAW="),
    ("SECTOR_DATA",   "var SECTOR_DATA"),
    ("INSIGHTS_DATA", "var INSIGHTS_DATA = "),
]

# Each new FINRA settlement period appends roughly 260 KB to the RAW block
# (33.3 -> 34.6 MB across the five periods added 2026-08). The old 35 MB
# ceiling was ~1 period of headroom away from firing on a healthy file, which
# would have turned this check into noise exactly when it needs to be trusted.
# The check exists to catch accidental deletion or a double-embed, so the
# ceiling only needs to sit well clear of normal growth.
SIZE_MIN_MB = 24
SIZE_MAX_MB = 50


# ── Helpers ───────────────────────────────────────────────────────────────────

def js_brace_balance(block):
    """
    Net unclosed braces in a JS block, skipping strings and comments.
    Returns 0 if balanced, >0 if truncated, <0 if extra closes.
    """
    depth = 0
    i = 0
    n = len(block)
    in_str = None
    while i < n:
        c = block[i]
        if in_str and c == '\\':
            i += 2
            continue
        if in_str:
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ('"', "'", '`'):
            in_str = c
            i += 1
            continue
        if c == '/' and i + 1 < n and block[i+1] == '/':
            while i < n and block[i] != '\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and block[i+1] == '*':
            i += 2
            while i + 1 < n and not (block[i] == '*' and block[i+1] == '/'):
                i += 1
            i += 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    return depth


def extract_script_blocks(html):
    blocks = []
    for m in re.finditer(r'<script[\s>]', html):
        start = m.start()
        end_tag = html.find('</script>', start)
        if end_tag == -1:
            continue
        content_start = html.find('>', start) + 1
        blocks.append((start, html[content_start:end_tag]))
    return blocks


# ── Runner ────────────────────────────────────────────────────────────────────

def run(path):
    errors   = []
    warnings = []
    passed   = []

    def ok(msg):
        passed.append(msg)
        print("  ✅  " + msg)

    def err(msg):
        errors.append(msg)
        print("  ❌  " + msg)

    def warn(msg):
        warnings.append(msg)
        print("  ⚠️   " + msg)

    print("\n" + "-"*60)
    print("  SI Dashboard Validator")
    print("  File: " + os.path.basename(path))
    print("-"*60 + "\n")

    if not os.path.exists(path):
        print("File not found: " + path)
        sys.exit(1)

    html      = open(path, encoding="utf-8").read()
    size_mb   = len(html) / 1e6
    file_len  = len(html)
    eof_start = file_len * 0.90

    # 1. File structure
    print("[ 1/11 ] File structure")
    if html.startswith("<!DOCTYPE html>"):
        ok("Starts with <!DOCTYPE html>")
    else:
        err("Does not start with <!DOCTYPE html>")

    if html.endswith("</html>"):
        ok("Ends with </html>")
    else:
        err("File does not end with </html> - likely truncated by Edit tool")

    bo = html.count("<body")
    bc = html.count("</body>")
    if bo == 1 and bc == 1:
        ok("Single <body>...</body> pair")
    else:
        err("<body> mismatch: " + str(bo) + " open, " + str(bc) + " close")

    # 2. Script tag balance
    print("\n[ 2/11 ] Script tag balance")
    opens  = len(re.findall(r"<script[\s>]", html))
    closes = html.count("</script>")
    if opens == closes:
        ok("<script> tags balanced (" + str(opens) + " pairs)")
    else:
        err("<script> mismatch: " + str(opens) + " open, " + str(closes) + " close")

    # 3. JS brace balance per script block
    print("\n[ 3/11 ] JS brace balance (catches mid-function truncation)")
    script_blocks = extract_script_blocks(html)
    for idx, (pos, block) in enumerate(script_blocks):
        net = js_brace_balance(block)
        pct = pos / file_len * 100
        label = "script block #" + str(idx+1) + " (at " + str(round(pct)) + "% of file)"
        if net == 0:
            ok(label + " - balanced")
        elif net > 0:
            err(label + " - " + str(net) + " unclosed braces (truncated inside block)")
        else:
            err(label + " - " + str(abs(net)) + " extra closing braces")

    # 4. Required JS symbols
    print("\n[ 4/11 ] Required JS functions / variables")
    for name, needle in REQUIRED_JS:
        if needle in html:
            ok(name)
        else:
            err("Missing: " + name + "  (expected '" + needle + "')")

    # 5. Function body sentinels
    print("\n[ 5/11 ] Function body sentinels (end-of-function markers)")
    for fn_name, sentinel in FUNCTION_SENTINELS:
        if sentinel in html:
            ok(fn_name + " body complete")
        else:
            err(fn_name + " body appears truncated - sentinel missing: '" + sentinel[:60] + "'")

    # 6. Near-EOF risk audit
    print("\n[ 6/11 ] Near-EOF risk audit (last 10% of file)")
    for symbol in NEAR_EOF_RISK_SYMBOLS:
        pos = html.find(symbol)
        if pos == -1:
            continue
        pct = pos / file_len * 100
        short = symbol[:50]
        if pos >= eof_start:
            warn("'" + short + "' at " + str(round(pct, 1)) + "% - in danger zone. "
                 "Use Python write instead of Edit tool for changes near here.")
        else:
            ok("'" + short + "' at " + str(round(pct, 1)) + "% - safe")

    # 7. DOM element IDs
    print("\n[ 7/11 ] Required DOM element IDs")
    for eid in REQUIRED_IDS:
        if 'id="' + eid + '"' in html:
            ok("#" + eid)
        else:
            err("Missing element: #" + eid)

    # 8. INSIGHTS_DATA JSON
    print("\n[ 8/11 ] INSIGHTS_DATA JSON")
    try:
        m = html.find("var INSIGHTS_DATA = ")
        if m == -1:
            raise ValueError("var INSIGHTS_DATA not found")
        brace = html.index("{", m)
        depth = 0
        i = brace
        in_str = False
        esc = False
        end = -1
        while i < len(html):
            c = html[i]
            if esc:
                esc = False
            elif c == "\\" and in_str:
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            i += 1
        if end == -1:
            raise ValueError("Could not find closing brace - INSIGHTS_DATA truncated")
        data   = json.loads(html[brace:end])
        themes = data.get("themes", [])
        ok("INSIGHTS_DATA valid JSON")
        n = len(themes)
        if n == EXPECTED_THEME_COUNT:
            ok(str(n) + " themes (expected " + str(EXPECTED_THEME_COUNT) + ")")
        else:
            warn(str(n) + " themes - expected " + str(EXPECTED_THEME_COUNT))
        ids = {t["id"] for t in themes}
        for tid in REQUIRED_THEMES:
            if tid in ids:
                ok("theme: " + tid)
            else:
                err("Missing theme: " + tid)
        for t in themes:
            if not t.get("constInfo"):
                warn("Theme '" + t["id"] + "' has empty constInfo")
    except Exception as ex:
        err("INSIGHTS_DATA parse failed: " + str(ex))

    # 9. Embedded data blocks
    print("\n[ 9/11 ] Embedded data blocks")
    for name, marker in DATA_MARKERS:
        if marker in html:
            ok(name + " present")
        else:
            err("Missing data block: " + name)

    # 10. UI threshold consistency
    print("\n[ 10/11 ] UI threshold consistency")
    for name, needle in THRESHOLD_CHECKS:
        alternatives = (needle,) if isinstance(needle, str) else needle
        if any(text in html for text in alternatives):
            ok(name)
        else:
            err("Threshold mismatch or missing: " + name)

    # 11. File size
    print("\n[ 11/11 ] File size")
    if SIZE_MIN_MB < size_mb < SIZE_MAX_MB:
        ok(str(round(size_mb, 2)) + " MB (within expected " + str(SIZE_MIN_MB) + "-" + str(SIZE_MAX_MB) + " MB)")
    else:
        err(str(round(size_mb, 2)) + " MB - outside expected range (" + str(SIZE_MIN_MB) + "-" + str(SIZE_MAX_MB) + " MB)")

    # Summary
    print("\n" + "-"*60)
    print("  " + str(len(passed)) + " passed  |  " + str(len(warnings)) + " warnings  |  " + str(len(errors)) + " errors")
    print("-"*60)

    if errors:
        print("\nERRORS:")
        for e in errors:
            print("     - " + e)
        print()
        sys.exit(1)
    elif warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print("     - " + w)
        print("\nDashboard is healthy (with warnings)\n")
    else:
        print("\nDashboard is healthy - all checks passed\n")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "si_dashboard.html"
    )
    run(target)
