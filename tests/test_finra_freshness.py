import io
import unittest
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import check_finra_freshness as fresh

CT = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
SCHEDULE = fresh.parse_schedule("06:17")
GRACE = timedelta(minutes=75)


def at(year, month, day, hour, minute, tz=CT):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


class FreshnessTests(unittest.TestCase):
    def assess(self, now, ingested, settlement, max_age=36):
        return fresh.assess(
            now, ingested, settlement,
            schedule=SCHEDULE, tz=CT, grace=GRACE, max_settlement_age_days=max_age,
        )

    def test_monday_before_grace_still_accepts_friday(self):
        status = self.assess(
            at(2026, 10, 19, 7, 0),
            at(2026, 10, 16, 6, 20),
            date(2026, 9, 30),
        )
        self.assertTrue(status.ok)
        self.assertIn("OK:", status.lines[0])

    def test_monday_after_grace_requires_monday_run(self):
        status = self.assess(
            at(2026, 10, 19, 8, 0),
            at(2026, 10, 16, 6, 20),
            date(2026, 9, 30),
        )
        self.assertFalse(status.ok)
        self.assertIn("STALE:", status.lines[0])
        self.assertIn("2026-10-19 06:17", status.lines[0])

    def test_fresh_monday_run(self):
        status = self.assess(
            at(2026, 10, 19, 8, 0),
            at(2026, 10, 19, 6, 20),
            date(2026, 9, 30),
        )
        self.assertTrue(status.ok)

    def test_saturday_requires_friday(self):
        status = self.assess(
            at(2026, 10, 17, 12, 0),
            at(2026, 10, 16, 6, 20),
            date(2026, 9, 30),
        )
        self.assertTrue(status.ok)
        missed = self.assess(
            at(2026, 10, 17, 12, 0),
            at(2026, 10, 15, 6, 20),
            date(2026, 9, 30),
        )
        self.assertFalse(missed.ok)

    def test_settlement_window_boundary(self):
        fresh_enough = self.assess(
            at(2026, 10, 19, 8, 0),
            at(2026, 10, 19, 6, 20),
            date(2026, 9, 13),
            max_age=36,
        )
        self.assertEqual((date(2026, 10, 19) - date(2026, 9, 13)).days, 36)
        self.assertTrue(fresh_enough.ok)
        missed = self.assess(
            at(2026, 10, 19, 8, 0),
            at(2026, 10, 19, 6, 20),
            date(2026, 9, 12),
            max_age=36,
        )
        self.assertFalse(missed.ok)
        self.assertIn("settlement file is missing", missed.lines[0])

    def test_empty_manifest(self):
        status = self.assess(at(2026, 10, 19, 8, 0), None, None)
        self.assertFalse(status.ok)
        self.assertIn("finra_ingest_runs has no completed load", status.lines[0])

    def test_naive_timestamp_is_treated_as_utc(self):
        status = self.assess(
            at(2026, 10, 19, 8, 0),
            datetime(2026, 10, 19, 11, 20),
            date(2026, 9, 30),
        )
        self.assertTrue(status.ok)

    def test_dst_weekday_slot(self):
        status = self.assess(
            at(2026, 11, 2, 8, 0),
            at(2026, 11, 2, 6, 20),
            date(2026, 10, 15),
        )
        self.assertTrue(status.ok)
        self.assertIn("CST", status.lines[0])

    def test_main_paths_and_secret_redaction(self):
        conn = Mock()
        cur = Mock()
        conn.cursor.return_value = nullcontext(cur)
        secret = "postgresql://user:supersecret@db.example/postgres"
        cur.fetchone.return_value = (
            datetime(2026, 10, 19, 11, 20, tzinfo=UTC),
            date(2026, 9, 30),
        )
        with patch.dict("os.environ", {"SUPABASE_DB_URL": secret}), \
             patch("psycopg.connect", return_value=conn), \
             patch.object(fresh, "current_time", return_value=datetime(2026, 10, 19, 13, 0, tzinfo=UTC)):
            out = io.StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(fresh.main([]), 0)
        self.assertIn("OK:", out.getvalue())
        self.assertNotIn("supersecret", out.getvalue())
        conn.close.assert_called_once()

        cur.fetchone.return_value = (
            datetime(2026, 10, 16, 11, 20, tzinfo=UTC),
            date(2026, 9, 30),
        )
        with patch.dict("os.environ", {"SUPABASE_DB_URL": secret}), \
             patch("psycopg.connect", return_value=conn), \
             patch.object(fresh, "current_time", return_value=datetime(2026, 10, 19, 13, 0, tzinfo=UTC)):
            err = io.StringIO()
            with patch("sys.stderr", err):
                self.assertEqual(fresh.main([]), 1)
        self.assertIn("STALE:", err.getvalue())
        self.assertNotIn("supersecret", err.getvalue())

        with patch.dict("os.environ", {}, clear=True):
            err = io.StringIO()
            with patch("sys.stderr", err):
                self.assertEqual(fresh.main([]), 2)
        self.assertIn("SUPABASE_DB_URL is not set", err.getvalue())

        with patch.dict("os.environ", {"SUPABASE_DB_URL": secret}), \
             patch("psycopg.connect", side_effect=RuntimeError(f"connect failed {secret}")):
            err = io.StringIO()
            with patch("sys.stderr", err):
                self.assertEqual(fresh.main([]), 2)
        self.assertNotIn("supersecret", err.getvalue())
        self.assertIn("Database connection failed", err.getvalue())


if __name__ == "__main__":
    unittest.main()
