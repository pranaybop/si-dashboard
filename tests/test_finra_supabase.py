import unittest
from unittest.mock import Mock, patch
from contextlib import nullcontext
from datetime import date

import ingest_finra_supabase as ingest


def payload(**changes):
    row = {c: '' for c in ingest.COLUMNS}
    row.update(settlementDate='2026-09-15', symbolCode='NA', issueName='Name',
               currentShortPositionQuantity='9007199254740993',
               previousShortPositionQuantity='5', averageDailyVolumeQuantity='2',
               changePreviousNumber='-3', daysToCoverQuantity='1.50')
    row.update(changes)
    return '|'.join(row) + '\n' + '|'.join(row.values()) + '\n'


class IngestionTests(unittest.TestCase):
    def test_parse_preserves_symbol_and_bigint(self):
        df = ingest.parse_period(payload(), '20260915')
        self.assertEqual(df.iloc[0]['symbol'], 'NA')
        self.assertEqual(df.iloc[0]['current_short'], 9007199254740993)
        self.assertEqual(df.iloc[0]['settlement_date'], date(2026, 9, 15))

    def test_bad_files_rejected(self):
        cases = [payload(settlementDate='2026-09-14'), payload(symbolCode=' '),
                 payload(currentShortPositionQuantity='oops'),
                 payload(currentShortPositionQuantity=''),
                 payload(currentShortPositionQuantity='1.5'),
                 payload(currentShortPositionQuantity=str(2**63)),
                 payload(daysToCoverQuantity='NaN'), 'symbolCode\nA\n',
                 '|'.join(ingest.COLUMNS) + '\n']
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                ingest.parse_period(text, '20260915')

    def test_duplicate_rejected(self):
        text = payload()
        with self.assertRaises(ValueError):
            ingest.parse_period(text + text.splitlines()[1] + '\n', '20260915')

    def test_http_missing_vs_failure(self):
        session = Mock()
        for status in [403, 404]:
            session.get.return_value.status_code = status
            self.assertIsNone(ingest.fetch_period('20260915', session))
        session.get.return_value.status_code = 500
        session.get.return_value.raise_for_status.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):
            ingest.fetch_period('20260915', session)

    def test_atomic_snapshot_and_manifest(self):
        conn = Mock()
        cur = Mock()
        conn.transaction.return_value = nullcontext()
        conn.cursor.return_value = nullcontext(cur)
        cur.copy.return_value = nullcontext(Mock())
        self.assertEqual(ingest.load_period(conn, ingest.parse_period(payload(), '20260915')), 1)
        queries = [c.args[0] for c in cur.execute.call_args_list]
        self.assertIn('pg_advisory_xact_lock', queries[0])
        self.assertTrue(any(q.startswith('delete from public.finra_short_interest') for q in queries))
        self.assertIn('public.finra_ingest_runs', queries[-1])

    def test_recent_loaded_period_is_refreshed(self):
        conn = Mock()
        cur = Mock()
        cur.fetchall.return_value = [(date(2026, 9, 15),)]
        conn.cursor.return_value = nullcontext(cur)
        with patch('sys.argv', ['ingest']), patch.dict('os.environ', {'SUPABASE_DB_URL': 'test'}), \
             patch('psycopg.connect', return_value=conn), \
             patch.object(ingest, 'candidate_dates', return_value=['20260915']), \
             patch.object(ingest, 'fetch_period', return_value=ingest.parse_period(payload(), '20260915')), \
             patch.object(ingest, 'load_period', return_value=1) as load:
            self.assertEqual(ingest.main(), 0)
            load.assert_called_once()
            conn.close.assert_called_once()

    def test_failure_is_nonzero_and_connection_closed(self):
        with patch('sys.argv', ['ingest', '--dry-run']), \
             patch.object(ingest, 'candidate_dates', return_value=['20260915']), \
             patch.object(ingest, 'fetch_period', side_effect=ValueError('bad data')):
            self.assertEqual(ingest.main(), 1)


if __name__ == '__main__':
    unittest.main()
