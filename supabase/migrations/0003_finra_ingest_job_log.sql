-- Proposed attempt log. Do not apply this from CI or from the ingest job.
-- Apply it by hand in the Supabase SQL editor when you want per-file
-- start/finish/status history. Until then, ingestion only writes
-- public.finra_ingest_runs (successful settlement snapshots).
--
-- check_finra_freshness.py reads finra_ingest_runs and does not need this table.
-- ingest_finra_supabase.py writes here only when the table already exists,
-- and a logging failure does not roll back a loaded settlement.

create table if not exists public.finra_ingest_job_log (
    id               bigint generated always as identity primary key,
    started_at       timestamptz not null default now(),
    finished_at      timestamptz,
    status           text        not null default 'running'
                         check (status in ('running', 'succeeded', 'failed')),
    row_count        integer,
    settlement_date  date,
    detail           text
);

create index if not exists finra_ingest_job_log_started_idx
    on public.finra_ingest_job_log (started_at desc);

-- Operational log: the Data API roles get no privileges and no policies.
-- The ingest connection (database owner) bypasses RLS. anon/authenticated
-- keep reading finra_short_interest and finra_ingest_runs only.
alter table public.finra_ingest_job_log enable row level security;

revoke all on table public.finra_ingest_job_log from public, anon, authenticated;
revoke all on sequence public.finra_ingest_job_log_id_seq from public, anon, authenticated;
