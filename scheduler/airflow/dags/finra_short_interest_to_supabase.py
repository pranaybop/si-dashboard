"""Weekday FINRA short-interest load into Supabase.

Airflow 3.3 Deadline Alerts fire if the 06:17 America/Chicago run is still
unfinished 75 minutes later (08:32 Eastern), which leaves time to notice before
09:00 Eastern. A second DAG at 07:45 runs only the freshness check, so a paused
or skipped ingest is still visible. Deadline Alerts are experimental in Airflow 3.
"""

from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, DeadlineAlert, DeadlineReference, SyncCallback

INGEST = "python /opt/finra/ingest_finra_supabase.py"
CHECK = "python /opt/finra/check_finra_freshness.py"
DISPATCH = """
set -eu
if [ -z "${GITHUB_DISPATCH_TOKEN:-}" ] || [ -z "${GITHUB_REPOSITORY:-}" ]; then
  echo "Pages dispatch skipped (set GITHUB_DISPATCH_TOKEN and GITHUB_REPOSITORY)"
  exit 0
fi
curl -fsS --retry 2 --max-time 30 -X POST \
  -H "Authorization: Bearer ${GITHUB_DISPATCH_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/dispatches" \
  -d '{"event_type":"finra-publish"}'
echo "Dispatched finra-publish to ${GITHUB_REPOSITORY}"
"""


def finra_task_failed(context):
    task_instance = context.get("task_instance")
    dag_id = getattr(task_instance, "dag_id", "finra")
    task_id = getattr(task_instance, "task_id", "unknown")
    print(f"FAILED: {dag_id} task {task_id} failed after retries", flush=True)


def finra_deadline_missed(**kwargs):
    context = kwargs.get("context") or {}
    print(
        "LATE: finra_short_interest_to_supabase missed its deadline "
        "(still unfinished 75 minutes after 06:17 America/Chicago). "
        f"context_keys={sorted(context)}",
        flush=True,
    )


DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(minutes=55),
    "on_failure_callback": finra_task_failed,
}

with DAG(
    dag_id="finra_short_interest_to_supabase",
    description="Weekday FINRA short interest ingest into Supabase, then freshness and Pages.",
    schedule="17 6 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=2),
    default_args=DEFAULT_ARGS,
    deadline=DeadlineAlert(
        reference=DeadlineReference.DAGRUN_LOGICAL_DATE,
        interval=timedelta(minutes=75),
        callback=SyncCallback(finra_deadline_missed),
    ),
    tags=["finra", "supabase"],
) as dag:
    ingest = BashOperator(task_id="ingest", bash_command=INGEST)
    freshness = BashOperator(task_id="freshness_check", bash_command=CHECK)
    dispatch = BashOperator(task_id="dispatch_pages", bash_command=DISPATCH)
    ingest >> freshness >> dispatch

with DAG(
    dag_id="finra_short_interest_freshness",
    description="Alarm if the morning FINRA ingest did not land.",
    schedule="45 7 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(minutes=10),
        "on_failure_callback": finra_task_failed,
    },
    tags=["finra", "supabase"],
) as freshness_dag:
    BashOperator(task_id="freshness_check", bash_command=CHECK)
