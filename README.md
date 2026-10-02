# FINRA Short Interest Tracker

Live dashboard: https://zyyzx.github.io/si-dashboard/si_dashboard.html

Bi-weekly FINRA short interest data for ~13,800 US-listed securities (Jan 2020 → latest settlement), with SI % of Float computed from Capital IQ historical float data, plus a candidates engine that surfaces sector/history outlier shorts.

## Folder layout

This is the single canonical project folder. Everything lives here. Tracked-in-git files are the source code and the built dashboard HTML; large data files are gitignored and rebuilt locally.

```
si-dashboard/
├── si_dashboard.html          # Built artifact (~33 MB, tracked)
├── analytics/                 # Pipeline module + parquet outputs (parquets gitignored)
├── exports/                   # Per-snapshot candidate CSVs (gitignored)
├── snapshots/                 # Historical backups (gitignored)
├── validate-dashboard-skill/  # Cowork skill package for the validator
└── *.py                       # Pipeline scripts
```

### Data files (gitignored, rebuilt locally)
- `si_history_full.csv` — Canonical FINRA history (~275 MB, ~3M rows). Master append target for `fetch_short_interest.py`.
- `equities.csv` — Equity universe metadata
- `analytics/features.parquet`, `analytics/candidates.parquet`, `analytics/float_panel.parquet`
- `capiq_float_historical.xlsx` — CapIQ template with `IQ_FLOAT` formulas
- `exports/{YYYYMMDD}/`, `exports/latest/` — Per-snapshot candidate CSVs

## Multi-machine setup

Clone the repo on the new machine, then bootstrap the data:

```powershell
git clone https://github.com/zyyzx/si-dashboard.git
cd si-dashboard
pip install requests pandas pyarrow openpyxl
$env:PYTHONIOENCODING = "utf-8"   # Windows console needs this for the print arrows

# 1. Bootstrap the FINRA history from scratch (~5 min for 150+ periods)
python fetch_short_interest.py

# 2. Build features + candidates + exports (~5 min)
python update_analytics.py

# 3. Refresh the Candidates tab on the dashboard
python add_candidates_tab.py
```

That's it. `git pull` brings down new dashboard HTML and any pipeline updates; rerun steps 1-3 locally whenever new FINRA periods land. Your data stays local; only the built dashboard and source code sync via GitHub.

## Refresh workflow on every FINRA publish

FINRA publishes settlements roughly every two weeks; new files land ~8 business days after the settlement date.

```powershell
$env:PYTHONIOENCODING = "utf-8"
python fetch_short_interest.py     # Appends new periods to si_history_full.csv
python update_analytics.py         # Rebuilds features, candidates, exports
python add_candidates_tab.py       # Re-patches the Candidates tab
python validate_dashboard.py       # Sanity check (54 checks)
git add si_dashboard.html
git commit -m "Refresh dashboard for FINRA YYYYMMDD"
git push
```

## Files

### Dashboard
- `si_dashboard.html` — Single-file dashboard (CSS, JS, data embedded). Deployed via GitHub Pages.

### Data pipeline
- `fetch_short_interest.py` — Downloads FINRA bi-weekly files, appends to `si_history_full.csv`
- `update_analytics.py` — Orchestrator: `build_features` → `build_candidates` → `export_candidates`
- `build_features.py` — Builds `analytics/features.parquet` from the history CSV
- `build_candidates.py` — Scores + applies gates, writes `analytics/candidates.parquet`
- `export_candidates.py` — Writes per-snapshot CSVs to `exports/{date}/` and mirrors `exports/latest/`
- `add_candidates_tab.py` — Idempotent patch that injects the Candidates tab into `si_dashboard.html`
- `build_actionable.py` — Actionable shorts screen: joins borrow cost/availability onto the SI candidates (see below)
- `add_actionable_tab.py` — Idempotent patch that injects the Actionable tab into `si_dashboard.html`
- `fetch_prices.py` — Fetches price history sampled at FINRA settlement dates → `prices_settlement.csv`
- `add_price_overlay.py` — Idempotent patch that adds the indexed price overlay to the Trend chart
- `price_coverage_report.py` — Ranks uncovered tickers by latest SI, so a coverage % can be judged
- `analytics/` — Python module: `loaders.py`, `features.py`, `score.py`, `borrow.py`
- `integrate_float_data.py` — Integrates CapIQ float data into the dashboard to compute SI % of Float
- `create_capiq_template.py` — Generates the CapIQ Excel template with `IQ_FLOAT` formulas
- `fix_smallcap_inclusion.py` — One-time fix to ensure SmallCap tickers are included
- `validate_dashboard.py` — Post-edit health check (54 validations including JS brace balance to catch Edit-tool truncation near EOF)

> Note: `build_dashboard.py` only generates a minimal 3-tab stub (Tracker / Screener / Help). It is **not** the canonical regenerator for the live 6-tab dashboard (Guide / Trend / Themes / SI Movers / Sector Sentiment / Screener) and will overwrite the rich version if run. Treat it as a prototype until a proper rich-dashboard regenerator is written.

## Actionable shorts screen (SI × borrow)

The candidates engine answers "which names look structurally short-worthy?"
It says nothing about whether the trade can be put on. `build_actionable.py`
joins the IBKR borrow layer onto the candidates to answer that:

```powershell
python update_analytics.py          # candidates.parquet must exist first
python build_actionable.py
python build_actionable.py --max-fee 5 --min-available 50000 --top 40
python add_actionable_tab.py        # inject the Actionable tab into the dashboard
python validate_dashboard.py
```

Writes `exports/{settlement}/actionable_shorts.csv` (+ `exports/latest/`, which
also receives `actionable_manifest.txt` — the tab reads its coverage counts from
there to render the in-tab coverage note).

`add_actionable_tab.py` is idempotent and byte-stable: re-running strips the
prior patch by sentinel before re-inserting, so it is safe on every refresh. It
uses pure Python string replacement, never the Edit tool — all dashboard JS
lives in the last 5% of a ~35 MB file, where the Edit tool has twice silently
truncated it (see `SI_TRACKER_PROJECT_NOTES.md` §7).

**Borrow data source.** `analytics/borrow.py` reads the IBKR poller's
`borrow.db` **read-only**, and reads it for borrow data only — fee, rebate,
share availability. si-dashboard stays canonical for short interest; the
`si_history` / `short_interest` tables inside `borrow.db` are a stale mirror
and a ~8-period window respectively, both unusable for the 6-month and 3-year
feature windows. Point the loader at the database with either:

```powershell
$env:BORROW_DB = "C:\path\to\borrow.db"          # full path, or
$env:BORROW_DATA_ROOT = "C:\path\to\borrow-data" # folder containing borrow.db
```

Default is `./borrow-data/borrow.db`. That folder is gitignored — the database
is ~285 MB, over GitHub's file limit, and is the system of record for
observations that cannot be re-collected.

**Two borrow layers, different shapes.** `borrow_daily` carries ~1 year of
daily bars for ~3,600 symbols (backfill capped near $300mm market cap) and is
what trend/percentile features are built from. `latest` + `symbol` carries the
current shortable snapshot for ~19,900 contracts — much broader, but no
history until the poller accumulates it. Candidates outside both layers are
**unmeasured, not bad borrows**; they are dropped by default, counted in the
run summary, and kept with `--include-uncovered`.

**Row flags:** `EARLY` (signal firing, borrow still cheap and in the low half
of its own year), `CROWDED` (fee in top decile or above the crowded cutoff —
squeeze risk), `TIGHTENING` (fee rising sharply; borrow is daily while FINRA
is bi-weekly and lagged ~9 days, so this often leads the next print),
`SHRINKING` (availability well below its 1-month average), `NO_BORROW`.

## Price overlay on the Trend chart

```powershell
# Fast path: one bulk archive, parsed locally (minutes)
#   1. Download "Daily / US / TXT" (d_us_txt.zip) from https://stooq.com/db/h/ in a browser
python fetch_prices.py --source stooq --stooq-zip "$env:USERPROFILE\Downloads\d_us_txt.zip"   # or the extracted folder

# Or per-ticker from Yahoo (hours for the full universe, but resumable)
python fetch_prices.py

python add_price_overlay.py         # embeds the series + adds the "Overlay price" toggle
python validate_dashboard.py
```

**Which source.** Stooq ships every US ticker in a single archive, so the
initial backfill takes minutes instead of the hours ~13.8K sequential Yahoo
requests need, with no rate limiting. Download the zip **in a browser** —
Stooq throttles and bot-blocks scripted pulls of its bulk archives, and a
manual download sidesteps that completely. Use Yahoo to top up a few names
between backfills, or if you specifically need a true dividend-adjusted close:
Stooq's bars are split-adjusted but not dividend-adjusted, so it fills
`close_adj` and leaves `close_raw` empty rather than claiming a tape price.
Both paths are resumable and write a `src` column recording the origin.

**Share classes.** FINRA writes multi-class names without punctuation —
Berkshire B is `BRKB` here — while both price sources hyphenate the class
letter (`brk-b.us.txt`, `BRK-B`). Each path reconciles this:

- *Stooq* tries every spelling of a filename stem against the real SI universe
  and keeps whichever exists.
- *Yahoo* tries the plain symbol first (always authoritative) and only falls
  back to the hyphenated form when it returns nothing. An aliased match must
  additionally pass an issuer-name check against `RAW.tickers[…].name`, so a
  delisted `XYZB` whose hyphenated form happens to be an unrelated live
  security is rejected rather than silently handed that company's prices. Rows
  resolved through an alias record it in the `src` column (`yahoo:BRK-B`) and
  the run prints an `aliased:` count, so the mapping is auditable instead of
  invisible.

`fetch_prices.py` writes `prices_settlement.csv` (gitignored) — one close per
(ticker, settlement date), sampled as the last close on or before each FINRA
settlement so it aligns index-for-index with `RAW.dates`. It is **resumable**:
rerunning skips tickers already fetched, so an interrupted run continues where
it stopped. Use `--limit 50` for a smoke test and `--pause` to go gentler on
the source.

**One axis, indexed — not a second y-scale.** SI and price have unrelated
scales, so the obvious move is a second y-axis. That move is wrong: a dual-axis
chart lets whoever draws it decide where the lines cross, manufacturing a
correlation the data does not contain. The overlay instead rebases both series
to 100 at the start of the selected window and puts them on one axis, reusing
the existing Normalized (Base=100) view. Ticking **Overlay price** therefore
switches to that view and says so. The tooltip shows the real close beside the
index value, so absolute price is never lost.

Price lines reuse their ticker's colour and are dashed — colour carries the
entity, dash carries the measure.

**Splits.** The embedded series is *adjusted* close (continuous through splits,
correct for an indexed comparison). FINRA share counts are as-reported and are
**not** retroactively split-adjusted, so a Shares Short line can step at a split
where the price line does not. SI % of float is immune, since numerator and
denominator scale together. `prices_settlement.csv` stores raw close too, for
anything that needs the as-printed tape.

**Judging coverage.** A percentage alone cannot tell you whether a gap
matters. `python price_coverage_report.py` ranks the uncovered tickers by
their latest short interest and splits them by whether they still report SI in
the most recent settlement — names that stopped reporting are delisted or
acquired and no current price source can fix them. Stooq's US archive covers
currently-listed NYSE/NASDAQ/NYSE American names plus ETFs (~6-7K securities),
so against a ~13.8K SI universe heavy with OTC and foreign tickers, roughly 43%
coverage is the structural ceiling for that source rather than a shortfall.
Use the report to confirm nothing liquid is missing; top up individual names
with `fetch_prices.py --tickers …` (Yahoo covers many OTC symbols Stooq does
not), which appends to the same CSV.

**Size.** The payload is run-encoded (`[startIdx, cents, cents, …]`) rather than
`[[idx, price], …]`, which costs ~5.8 bytes per point instead of ~13. The full
~13.8K-ticker universe adds roughly 10 MB, taking the dashboard to ~45 MB —
inside the validator's 50 MB ceiling. To trade coverage for size, fetch a
smaller universe (`fetch_prices.py --tickers …`); the injector embeds whatever
is in the CSV. `add_price_overlay.py --remove` strips the overlay cleanly.

### Reference
- `quarterly_dates.json` — 26 quarterly dates used for CapIQ float snapshots
- `all_tickers_for_float.txt` — Full ticker list for float data
- `tickers_needing_float.txt` — Tickers still missing float coverage
- `short interest CapIQ.txt` — CapIQ field reference notes
- `SI_TRACKER_PROJECT_NOTES.md` — Detailed project documentation (canonical engineering notes)
- `ISSUES.md` — Known issues and fixes

## Scheduled FINRA ingestion

The optional PostgreSQL collector and GitHub Actions schedule are documented in
[SUPABASE_SETUP.md](SUPABASE_SETUP.md). Deployment requires applying migrations
and setting the private `SUPABASE_DB_URL` Actions secret. After ingestion, the workflow builds and validates the embedded dashboard
from Supabase and publishes it to GitHub Pages. Visitors can view it for free
without a login. See the setup guide for Pages configuration and data freshness
limitations.
