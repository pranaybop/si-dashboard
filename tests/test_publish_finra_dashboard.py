import copy
from datetime import date
import unittest

import publish_finra_dashboard as publish


class DashboardTests(unittest.TestCase):
    def test_revisions_removals_and_no_invented_float(self):
        template = {'dates':['20260115','20260130'], 'tickers':{
            'A': {'name':'A','si':[[0,100],[1,200]],'pct':[[0,10],[1,20]]},
            'B': {'name':'B','si':[[0,50]],'pct':[]}}}
        dates = ['20260115','20260130','20260213']
        rows = [(date(2026,1,15),'A','Name <script>',150),
                (date(2026,2,13),'A','Name <script>',300)]
        result = publish.refresh_raw(template, dates, rows)
        self.assertEqual(result['tickers']['A']['si'], [[0,150],[2,300]])
        self.assertEqual(result['tickers']['A']['pct'], [[0,15]])
        self.assertEqual(result['tickers']['B']['si'], [])
        self.assertNotIn('<script>', result['tickers']['A']['name'])
        self.assertEqual(template['tickers']['A']['si'], [[0,100],[1,200]])

    def test_prices_align_by_date_with_inserted_period(self):
        prices = {'A':[0,100,200]}
        result = publish.remap_prices(prices,['20260115','20260213'], ['20260115','20260130','20260213'])
        self.assertEqual(result, {'A':[0,100,None,200]})

    def test_analytics_dates_and_absent_latest(self):
        dates = [f'2026{i:04d}' for i in range(30)]
        raw = {'dates':dates,'tickers':{
            'A':{'name':'A','si':[[i,100+i*i] for i in range(30)],'pct':[]},
            'B':{'name':'B','si':[[0,50]],'pct':[]}}}
        sectors = {'dates':[], 'A':{'constInfo':[{'t':'A'}], 'constituents':[['A','B']]}}
        insights = {'dates':[], 'themes':[{'constInfo':[{'t':'A'},{'t':'B'}], 'constSeries':[]}]}
        sector, insight = publish.refresh_analytics(raw, sectors, insights)
        self.assertEqual(sector['dates'], dates)
        self.assertEqual(insight['dates'], dates)
        self.assertEqual(len(insight['themes'][0]['aggSI']),30)
        self.assertEqual(insight['themes'][0]['constInfo'][1]['latest'],None)
        for key in ['rising','covering','rising_2w','covering_2w','rising_6w','covering_6w']:
            self.assertTrue(all(r['t'] != 'B' for r in insight[key]))

    def test_block_script_escape(self):
        source = 'const RAW={"value":0};'
        changed = publish.replace_block(source,'RAW',{'value':'</script>'})
        self.assertNotIn('</script>',changed)
        self.assertEqual(publish.block(changed,'RAW')[0],{'value':'</script>'})

    def test_zscore_ignores_current_observation_in_baseline(self):
        series = [100]*29 + [200]
        # Prior changes have zero variance: do not invent a finite signal.
        self.assertIsNone(publish.growth_z(series,1))
        self.assertIsNone(publish.growth(100,None))
        self.assertEqual(publish.indexed([None,100,0,200]),[None,100,0,200])


class UniverseExpandTests(unittest.TestCase):
    def test_expand_template_from_candidates(self):
        template = {'dates': ['20260115'], 'tickers': {
            'A': {'name': 'A', 'si': [[0, 1]], 'pct': [[0, 1.0]]}}}
        newcomers = {
            'FRVO': 'Fervo Energy Corp',
            'X': 'Name <script>alert(1)</script>',
        }
        added = publish.expand_template_tickers(template, newcomers)
        self.assertEqual(added, 2)
        self.assertIn('FRVO', template['tickers'])
        self.assertEqual(template['tickers']['FRVO']['si'], [])
        self.assertEqual(template['tickers']['FRVO']['pct'], [])
        self.assertEqual(template['tickers']['FRVO']['name'], 'Fervo Energy Corp')
        self.assertNotIn('<script>', template['tickers']['X']['name'])
        self.assertIn('&lt;', template['tickers']['X']['name'])
        # Existing ticker unchanged.
        self.assertEqual(template['tickers']['A']['si'], [[0, 1]])

    def test_refresh_raw_accepts_newly_seeded_symbols(self):
        template = {'dates': ['20260115'], 'tickers': {
            'A': {'name': 'A', 'si': [], 'pct': []}}}
        publish.expand_template_tickers(template, {'FRVO': 'Fervo Energy Corp'})
        dates = ['20260115', '20260130']
        rows = [
            (date(2026, 1, 15), 'A', 'A Inc', 100),
            (date(2026, 1, 30), 'FRVO', 'Fervo Energy Corp', 2500),
        ]
        result = publish.refresh_raw(template, dates, rows)
        self.assertEqual(result['tickers']['FRVO']['si'], [[1, 2500]])
        self.assertEqual(result['tickers']['FRVO']['pct'], [])
        self.assertEqual(result['tickers']['A']['si'], [[0, 100]])

    def test_name_filtering_gates(self):
        self.assertTrue(publish.is_eligible_new_symbol('FRVO', 'Fervo Energy Corp'))
        self.assertTrue(publish.is_eligible_new_symbol('BRKB', 'Berkshire Hathaway Inc Class B'))
        self.assertTrue(publish.is_eligible_new_symbol('UAL', 'United Airlines Holdings Inc'))
        self.assertFalse(publish.is_eligible_new_symbol('XYZW', 'Acme Warrant 2027'))
        self.assertFalse(publish.is_eligible_new_symbol('XYZR', 'Acme Rights'))
        self.assertFalse(publish.is_eligible_new_symbol('XYZU', 'Acme Unit'))
        self.assertFalse(publish.is_eligible_new_symbol('XYZP', 'Acme Preferred Stock'))
        self.assertFalse(publish.is_eligible_new_symbol('XYZN', 'Acme Senior Note'))
        self.assertFalse(publish.is_eligible_new_symbol('XYZD', 'Acme American Depositary Shares'))
        self.assertFalse(publish.is_eligible_new_symbol('XYZA', 'Acme ADR'))
        self.assertFalse(publish.is_eligible_new_symbol('TOOLONG', 'Valid Name'))
        self.assertFalse(publish.is_eligible_new_symbol('AB-C', 'Hyphenated'))
        self.assertFalse(publish.is_eligible_new_symbol('', 'Empty'))
        self.assertFalse(publish.is_eligible_new_symbol('A1', 'Foo Warrant Bar'))

    def test_max_new_cap(self):
        # Simulate DB rows: mix of eligible and gated names, more than max_new.
        class FakeConn:
            def execute(self, sql, params=None):
                rows = [
                    ('AAA', 'Alpha Inc'),
                    ('AAB', 'Beta Warrant'),  # gated
                    ('AAC', 'Gamma Corp'),
                    ('AAD', 'Delta Preferred'),  # gated
                    ('AAE', 'Epsilon Inc'),
                    ('AAF', 'Zeta Inc'),
                ]
                class Result:
                    def fetchall(self_inner):
                        return rows
                return Result()

        known = {'OLD'}
        got = publish.select_new_tickers(FakeConn(), known, max_new=2)
        self.assertEqual(list(got.keys()), ['AAA', 'AAC'])
        self.assertEqual(got['AAA'], 'Alpha Inc')
        empty = publish.select_new_tickers(FakeConn(), known, max_new=0)
        self.assertEqual(empty, {})


if __name__ == '__main__':
    unittest.main()
