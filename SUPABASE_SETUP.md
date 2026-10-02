# FINRA ingestion into Supabase

The workflow ingests public FINRA snapshots into PostgreSQL, then patches and
validates the rich dashboard from a consistent database snapshot and deploys it
to GitHub Pages. Visitors need no account or database access. The repository HTML
is the UI template; the freshly published artifact is built in `site/`.

## Deploy

1. In the Supabase project SQL Editor, execute
   `supabase/migrations/0001_finra_short_interest.sql`, then
   `supabase/migrations/0002_finra_read_permissions.sql`. Alternatively apply
   both through the linked Supabase CLI migration workflow. The ingestion job
   deliberately does not apply schema changes.
2. In Supabase **Connect**, select **Session pooler**, URI format, port **5432**.
   Insert the database password (URL-encode reserved characters). Use a database
   owner account such as `postgres.<project-ref>`. Store the complete URI in
   GitHub Settings → Secrets and variables → Actions as `SUPABASE_DB_URL`.
   Do not commit it. The script requires TLS. Direct connections also work with
   suitable network access; Supabase direct connections normally require IPv6,
   whereas the session pooler supports IPv4 on GitHub runners.
3. Push and merge the changes onto the default branch and enable GitHub Actions.
   If you have Pages administration access, set Source to **GitHub Actions**.
   The existing branch-based Pages configuration also supports this deployment.
   Manually run **FINRA fetch to Supabase**, with `backfill` checked, to load
   history. Check logs for failures and inspect `public.finra_ingest_runs`.
4. The schedule polls weekdays at **14:17 UTC**. It discovers files by probing
   settlement weekdays, rather than relying on an exact publication time.
   Successful periods and their manifest commit together. Recent 45-day periods
   are always reloaded to capture corrections, including removed symbols.
5. For corrections older than 45 days, manually dispatch with both `backfill`
   and `refresh` checked. For outages longer than 45 days, run `backfill` to
   recover missing historical periods. Historical backfill uses the known dates
   in `fetch_short_interest.py`, currently through 2026; extend that list for
   subsequent years. Normal recent discovery continues past 2026 automatically.

Missing-file 403/404 responses are expected during discovery. Other HTTP errors,
invalid files, and failed database loads fail the job, while other periods can
still commit. A 403/404 for a previously loaded period fails rather than silently
claiming success. An access block affecting only unobserved dates can still look
like missing files; monitor the newest settlement against FINRA's calendar.
GitHub schedules can be delayed and run only from the default branch; public
repositories disable scheduled workflows after 60 days without activity, and
forks start with schedules disabled. Check Actions periodically and enable the
workflow again if needed.

## Verify locally

```sh
python -m pip install -r requirements-finra.txt
python -m unittest discover -s tests -p 'test_finra_supabase.py'
python ingest_finra_supabase.py --dry-run
# With SUPABASE_DB_URL provided privately in the environment:
python ingest_finra_supabase.py --backfill
```

The API roles `anon` and `authenticated` receive SELECT only, with matching
RLS policies; ingestion uses the privileged database connection. Never expose
the database URI or a service-role key in browser code. The dashboard embeds public data produced by the build; the database secret
is never included in the deployment artifact.
Measure database and index size after backfill before assuming the full history
and future datasets fit the project's free-tier allocation.

References: [Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Public dashboard publication

After successful ingestion the `build` job runs `publish_finra_dashboard.py`,
validates the resulting HTML, and uploads only `site/` to Pages. The `deploy`
job publishes it to https://zyyzx.github.io/si-dashboard/si_dashboard.html (the
root URL redirects there). On a build/validation failure the previous site stays
published. No generated HTML commits or extra credentials are needed. If legacy branch
publishing is enabled, the workflow also runs after `pages-build-deployment`
finishes on main, so a template build is followed by the database-backed build. The
`github-pages` environment must allow deployments from the default branch.

The build retains the existing curated ticker universe and theme/sector
membership, replacing its FINRA share observations with database history.
The database holds the broader FINRA universe; adding new tracked tickers is a
separate universe/metadata refresh. All three FINRA data blocks share the same
settlement dates. Missing observations remain missing. Screener endpoints require
actual observations, avoiding stale carry-forward. Price runs are remapped by
date. Same-date SI corrections recalculate historical SI% using the previously
published float denominator; new settlement dates have no inferred float ratios.
The page defaults to raw Shares Short and displays separate freshness dates for
float ratios, prices, and Candidates. Market caps and basket membership remain
snapshots. Candidates and borrow analytics still need their separate sources.

Movers compare the latest 1-, 3-, or 13-period SI percent change against prior
changes in the preceding 72 periods (population standard deviation, at least 12
valid prior changes). Missing endpoints and zero denominators produce no signal;
zero variance produces no z-score. Theme heat averages available constituent
13-period z-scores; constituents with z >= 1.5 count as rising. These raw-share
signals are sensitive to stock splits. This methodology is now implemented in
Python; the previous precomputed analytics had no reproducible builder here.

For a local publication build, supply the private DB URL in the environment:
`python publish_finra_dashboard.py --output-dir site`. The publisher verifies
all manifest row counts from the same repeatable-read snapshot before building.
The repository template is not overwritten by scheduled builds. Reload an open
dashboard to see the most recently published version.
