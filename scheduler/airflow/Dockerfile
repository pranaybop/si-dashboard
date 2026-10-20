FROM apache/airflow:3.3.2

COPY requirements-finra.txt /tmp/requirements-finra.txt
RUN pip install --no-cache-dir -r /tmp/requirements-finra.txt
COPY --chown=airflow:0 fetch_short_interest.py ingest_finra_supabase.py check_finra_freshness.py /opt/finra/
