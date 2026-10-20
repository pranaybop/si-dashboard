#!/bin/sh
# One entrypoint for the normal ingest and the explicit modes schedulers use.
# Flags (--backfill, --refresh, --since-last, --dry-run) go straight to ingest.
# `check` runs the freshness probe. `refresh-cycle` ingests, checks, then
# optionally asks GitHub Actions to publish Pages.
set -eu

if [ "$#" -eq 0 ]; then
  exec python /app/ingest_finra_supabase.py
fi

case "$1" in
  check|freshness)
    shift
    exec python /app/check_finra_freshness.py "$@"
    ;;
  refresh-cycle)
    shift
    exec /usr/local/bin/run_finra_refresh "$@"
    ;;
  *)
    exec python /app/ingest_finra_supabase.py "$@"
    ;;
esac
