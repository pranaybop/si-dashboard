-- FINRA bi-weekly short interest, one row per (settlement date, symbol).
create table if not exists public.finra_short_interest (
    settlement_date   date    not null,
    symbol            text    not null,
    issue_name        text,
    exchange_code     text,
    market_class      text,
    current_short     bigint,
    previous_short    bigint,
    stock_split_flag  text,
    avg_daily_volume  bigint,
    days_to_cover     numeric,
    revision_flag     text,
    change_pct        numeric,
    change_prev       bigint,
    ingested_at       timestamptz not null default now(),
    primary key (settlement_date, symbol)
);

-- Per-symbol time series lookups (ticker detail chart).
create index if not exists finra_si_symbol_date_idx
    on public.finra_short_interest (symbol, settlement_date);

-- One row per settlement file processed; records committed snapshot counts.
create table if not exists public.finra_ingest_runs (
    settlement_date date primary key,
    row_count       integer not null,
    ingested_at     timestamptz not null default now()
);

-- The dashboard reads with the public anon key: read-only, no writes.
-- The ingest job connects with the database URL (bypasses RLS).
alter table public.finra_short_interest enable row level security;
alter table public.finra_ingest_runs    enable row level security;

drop policy if exists "anon read short interest" on public.finra_short_interest;
create policy "anon read short interest" on public.finra_short_interest
    for select to anon using (true);

drop policy if exists "anon read ingest runs" on public.finra_ingest_runs;
create policy "anon read ingest runs" on public.finra_ingest_runs
    for select to anon using (true);
