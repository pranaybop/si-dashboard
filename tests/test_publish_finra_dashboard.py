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


if __name__ == '__main__':
    unittest.main()
