import ast
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class SchedulerContractTests(unittest.TestCase):
    def test_workflow_publishes_on_repository_dispatch_without_ingesting(self):
        text = (ROOT / ".github/workflows/finra_fetch.yml").read_text()
        loaded = yaml.safe_load(text)
        triggers = loaded.get("on", loaded.get(True))
        self.assertIn("finra-publish", triggers["repository_dispatch"]["types"])
        self.assertIn("repository_dispatch", loaded["jobs"]["ingest"]["if"])
        self.assertIn("needs.ingest.result == 'skipped'", loaded["jobs"]["build"]["if"])
        self.assertIn("workflow_dispatch", triggers)
        self.assertIn("schedule", triggers)

    def test_airflow_dag_contract(self):
        path = ROOT / "scheduler/airflow/dags/finra_short_interest_to_supabase.py"
        source = path.read_text()
        ast.parse(source)
        self.assertIn('dag_id="finra_short_interest_to_supabase"', source)
        self.assertIn('dag_id="finra_short_interest_freshness"', source)
        self.assertIn('schedule="17 6 * * 1-5"', source)
        self.assertIn('schedule="45 7 * * 1-5"', source)
        self.assertIn("America/Chicago", source)
        self.assertIn('"retries": 2', source)
        self.assertIn("DeadlineAlert", source)
        self.assertIn("timedelta(minutes=75)", source)
        self.assertIn("finra_deadline_missed", source)

    def test_shell_scripts_parse(self):
        scripts = [
            ROOT / "docker/entrypoint.sh",
            ROOT / "scheduler/run_finra_refresh.sh",
            ROOT / "scheduler/cloud-run/setup.sh",
        ]
        for script in scripts:
            subprocess.run(["sh", "-n", str(script)], check=True)
        cycle = (ROOT / "scheduler/run_finra_refresh.sh").read_text()
        self.assertIn("finra-publish", cycle)
        self.assertIn("check_finra_freshness.py", cycle)

    def test_optional_job_log_migration_is_locked_down(self):
        sql = (ROOT / "supabase/migrations/0003_finra_ingest_job_log.sql").read_text().lower()
        for column in ("started_at", "finished_at", "status", "row_count", "settlement_date"):
            self.assertIn(column, sql)
        self.assertIn("enable row level security", sql)
        self.assertIn("revoke all", sql)
        self.assertIn("do not apply", sql)


if __name__ == "__main__":
    unittest.main()
