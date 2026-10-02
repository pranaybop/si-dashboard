# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

A FINRA bi-weekly short-interest tracker (~13.8K US securities, 2020 → present). The deliverable is **`si_dashboard.html`**, a single ~35–45 MB self-contained file (CSS, JS, and all data embedded as `const RAW = {...}`) served via GitHub Pages. The dashboard has no server or build/lint tooling; optional Supabase ingestion has focused unittest checks — just flat Python scripts that read CSVs/parquets and either write derived files or patch the HTML in place.

`README.md` is the operational reference (refresh workflow, price overlay, actionable screen); `SI_TRACKER_PROJECT_NOTES.md` has the engineering history, including the `RAW`/`INSIGHTS_DATA` structures and past failures (§6–7). `ISSUES.md` is a data-accuracy backlog.

## Commands

Run from the repo root. On Windows set `$env:PYTHONIOENCODING="utf-8"` (scripts print non-ASCII arrows). Dependencies: `requests pandas pyarrow openpyxl` (no requirements.txt).

```
python fetch_short_interest.py     # download new FINRA periods -> append to si_history_full.csv
python update_analytics.py         # build_features -> build_candidates -> export_candidates
python add_candidates_tab.py       # patch Candidates tab into si_dashboard.html
python build_actionable.py && python add_actionable_tab.py   # optional: SI x IBKR borrow screen
python fetch_prices.py && python add_price_overlay.py        # optional: Trend-chart price overlay
python validate_dashboard.py       # ALWAYS run after touching the HTML (exit 1 on failure)
```

Single-step runs: each `build_*.py` / `export_candidates.py` has `main()` and can be run alone. `fetch_prices.py --limit 50` is the smoke-test mode; `add_price_overlay.py --remove` strips the overlay.

## Architecture

**Data flow:** FINRA CDN (`cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv`, pipe-delimited) → `si_history_full.csv` (canonical, gitignored, ~3M rows) → `analytics/` package → `analytics/*.parquet` → `exports/{date}/*.csv` and HTML patches.

- `analytics/__init__.py` defines every input/output path. `loaders.py` reads sources read-only; `features.py` builds the per-(ticker, settlement_date) feature table (SI, DTC, float, sector/history z-scores); `score.py` applies gates and signals (currently only Signal D, "sector & history outlier short"); `borrow.py` reads the IBKR poller's SQLite `borrow.db` read-only (path via `$BORROW_DB` / `$BORROW_DATA_ROOT`, default `./borrow-data/borrow.db`).
- Float comes from Capital IQ (`capiq_float_historical.xlsx` → `float_panel.parquet`); `integrate_float_data.py` folds SI % of float into the dashboard. CapIQ is manual (Excel plugin), not scriptable.
- `fetch_short_interest.py` hardcodes `SETTLEMENT_DATES` through 2026 and probes each one; HTTP 404 means "not published yet". New years/dates must be added there by hand. It rewrites the entire CSV on each run (concat + dedupe on `settlementDate`,`symbolCode` + sort), and `YEAR` in its config is unused.
- `si_history_full.csv` column names are FINRA's (`symbolCode`, `currentShortPositionQuantity`, `settlementDate` as `YYYYMMDD` strings, …); `loaders.py` renames them to `ticker`, `si_shares`, `settlement_date`.

**Dashboard is patched, not regenerated.** The live multi-tab dashboard (Guide / Trend / Themes / SI Movers / Sector Sentiment / Screener + Candidates / Actionable) is the committed HTML mutated by `add_*_tab.py`, `add_price_overlay.py`, `integrate_float_data.py`, `append_period_to_dashboard.py`. `build_dashboard.py`, `create_simple_dashboard.py`, and `regenerate_dashboard.py` produce a minimal stub and **will overwrite the rich dashboard** — do not run them against `si_dashboard.html`.

## Editing the dashboard HTML

- **Never use the Edit tool on `si_dashboard.html`.** All JS lives in the last ~5% of a 35 MB+ file, and Edit has silently truncated it twice. Patch with Python string replacement (read, replace, write) and run `validate_dashboard.py` afterward (it checks JS brace balance, required symbols, near-EOF risk, and size bounds, with a 50 MB ceiling).
- Patch scripts are **idempotent via sentinels**: re-running strips the prior injected block before re-inserting. Preserve that property in any new patcher.
- Embedded data shapes: `RAW.dates` is the settlement-date list; `RAW.tickers[T].si` is index-aligned to it; price series are run-encoded `[startIdx, cents, ...]` and must align index-for-index with `RAW.dates`.
- `validate-dashboard-skill/` is a packaged copy of the validator for Cowork; keep it in sync with the root `validate_dashboard.py` if you change checks.

## Gotchas

- `*.csv`, `*.parquet`, `exports/`, `snapshots/`, `borrow-data/`, `.Codex/` are gitignored; a fresh clone must rebuild data (`fetch_short_interest.py` bootstraps ~150 periods in ~5 min). Only code and `si_dashboard.html` are committed.
- FINRA writes share classes without punctuation (`BRKB`); price sources hyphenate (`BRK-B`). `fetch_prices.py` reconciles this with an issuer-name check — keep it when touching ticker matching.
- FINRA share counts are not split-adjusted; SI % of float is immune, raw Shares Short is not.
- Candidates with no borrow data are "unmeasured", not "bad borrow" — don't treat missing as failing.

## Supabase ingestion (deployment pending)

`ingest_finra_supabase.py` loads validated FINRA files into PostgreSQL using
`SUPABASE_DB_URL`; `.github/workflows/finra_fetch.yml` polls weekdays and supports
manual backfill/refresh. Apply both `supabase/migrations/` files before ingestion.
See `SUPABASE_SETUP.md` for secret and deployment setup. Recent periods refresh
for corrections; historical backfill uses `fetch_short_interest.SETTLEMENT_DATES`.
`publish_finra_dashboard.py` reads a consistent DB snapshot, refreshes RAW,
SECTOR_DATA and INSIGHTS_DATA in a deployment copy, remaps prices, and runs the
validator. The workflow uploads `site/` and deploys via GitHub Pages Actions.
Set Pages Source to GitHub Actions. The committed HTML remains the UI template;
use Python patches to edit it and validate afterward. Float ratios for new dates,
Candidates, borrow, market caps and ticker/basket membership require separate
source refreshes. The published dashboard displays their freshness limits.
