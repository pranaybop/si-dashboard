# One-shot FINRA short-interest ingest. SUPABASE_DB_URL comes from the environment
# at run time and is never baked into the image.
#
#   docker build -t finra-ingest:local .
#   docker run --rm -e SUPABASE_DB_URL finra-ingest:local
#   docker run --rm -e SUPABASE_DB_URL finra-ingest:local --backfill
#   docker run --rm -e SUPABASE_DB_URL finra-ingest:local --backfill --refresh
#   docker run --rm -e SUPABASE_DB_URL finra-ingest:local --since-last
#   docker run --rm -e SUPABASE_DB_URL finra-ingest:local check
#   docker run --rm -e SUPABASE_DB_URL finra-ingest:local refresh-cycle

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 finra

WORKDIR /app

COPY requirements-finra.txt .
RUN pip install --no-cache-dir -r requirements-finra.txt

COPY fetch_short_interest.py ingest_finra_supabase.py check_finra_freshness.py ./
COPY scheduler/run_finra_refresh.sh /usr/local/bin/run_finra_refresh
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh /usr/local/bin/run_finra_refresh \
    && chown -R finra:finra /app

USER finra

ENTRYPOINT ["/entrypoint.sh"]
