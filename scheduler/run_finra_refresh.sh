#!/bin/sh
# Ingest, then freshness, then optional Pages dispatch.
# Extra arguments are passed to ingest_finra_supabase.py (--backfill, --since-last, ...).
# Secrets stay in the environment: SUPABASE_DB_URL, and optionally
# GITHUB_DISPATCH_TOKEN, GITHUB_REPOSITORY, FINRA_HEALTHCHECK_URL.
set -eu

ping() {
  if [ -n "${FINRA_HEALTHCHECK_URL:-}" ]; then
    # A missed ping is how an external dead-man's switch notices a dead host.
    # Failure to reach the ping endpoint must not hide an ingest result.
    curl -fsS --retry 2 --max-time 15 "${FINRA_HEALTHCHECK_URL}$1" >/dev/null || true
  fi
}

fail() {
  ping "/fail"
  exit "$1"
}

ping "/start"
set +e
python /app/ingest_finra_supabase.py "$@"
ingest_rc=$?
set -e
if [ "$ingest_rc" -ne 0 ]; then
  echo "Ingest failed (exit ${ingest_rc}); Pages dispatch skipped" >&2
  fail "$ingest_rc"
fi

set +e
python /app/check_finra_freshness.py
fresh_rc=$?
set -e
if [ "$fresh_rc" -ne 0 ]; then
  echo "Freshness check failed (exit ${fresh_rc}); Pages dispatch skipped" >&2
  fail "$fresh_rc"
fi

if [ -n "${GITHUB_DISPATCH_TOKEN:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  curl -fsS --retry 2 --max-time 30 -X POST \
    -H "Authorization: Bearer ${GITHUB_DISPATCH_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/dispatches" \
    -d '{"event_type":"finra-publish"}'
  echo "Dispatched finra-publish to ${GITHUB_REPOSITORY}"
else
  echo "Pages dispatch skipped (set GITHUB_DISPATCH_TOKEN and GITHUB_REPOSITORY to publish)"
fi

ping ""
exit 0
