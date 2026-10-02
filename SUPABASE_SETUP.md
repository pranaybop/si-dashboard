# FINRA ingestion into Supabase

The workflow ingests public FINRA snapshots into PostgreSQL. It does not yet
rebuild or publish `si_dashboard.html`; that dashboard still uses embedded RAW
and the local analytics/float/borrow/price pipeline.

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
the database URI or a service-role key in browser code. Dashboard live reads,
pagination, analytics refresh, and Pages publication remain separate work.
Measure database and index size after backfill before assuming the full history
and future datasets fit the project's free-tier allocation.

References: [Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
