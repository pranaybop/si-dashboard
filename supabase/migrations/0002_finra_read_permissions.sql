-- RLS policies do not confer table privileges. Explicitly restrict API roles,
-- including projects with permissive default grants. Ingestion uses postgres.
grant usage on schema public to anon, authenticated;
revoke all on public.finra_short_interest, public.finra_ingest_runs from anon, authenticated;
grant select on public.finra_short_interest, public.finra_ingest_runs to anon, authenticated;

drop policy if exists "authenticated read short interest" on public.finra_short_interest;
create policy "authenticated read short interest" on public.finra_short_interest
    for select to authenticated using (true);
drop policy if exists "authenticated read ingest runs" on public.finra_ingest_runs;
create policy "authenticated read ingest runs" on public.finra_ingest_runs
    for select to authenticated using (true);
