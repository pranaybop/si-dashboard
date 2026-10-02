#!/usr/bin/env python3
"""Patch the rich dashboard from a consistent Supabase snapshot; never regenerate UI.

The existing curated ticker universe and basket definitions remain the template.
Float ratios are retained only for dates with a published denominator; new dates
have no SI% until float is refreshed. Prices are remapped by settlement date.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys

from append_period_to_dashboard import patch_static_markup

ROOT = Path(__file__).resolve().parent


def block(source, name):
    match = re.search(r'(?:const|var|let) ' + re.escape(name) + r'\s*=\s*', source)
    if not match:
        raise ValueError(f'Missing {name} data block')
    value, end = json.JSONDecoder().raw_decode(source, match.end())
    return value, match.end(), end


def replace_block(source, name, value):
    _, start, end = block(source, name)
    # Prevent source strings from closing the surrounding script element.
    encoded = json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    return source[:start] + encoded + source[end:]


def remap_pairs(pairs, old_dates, new_dates):
    indices = {d: i for i, d in enumerate(new_dates)}
    return sorted([indices[old_dates[i]], v] for i, v in pairs if old_dates[i] in indices)


def remap_prices(prices, old_dates, new_dates):
    out = {}
    indices = {d: i for i, d in enumerate(new_dates)}
    for symbol, run in prices.items():
        pairs = {indices[old_dates[run[0] + k]]: v for k, v in enumerate(run[1:])
                 if run[0] + k < len(old_dates) and old_dates[run[0] + k] in indices}
        if pairs:
            first, last = min(pairs), max(pairs)
            out[symbol] = [first] + [pairs.get(i) for i in range(first, last + 1)]
    return out


def indexed(series):
    base = next((v for v in series if v is not None and v > 0), None)
    return [round(v / base * 100, 1) if base and v is not None else None for v in series]


def growth(a, b):
    return round((a / b - 1) * 100, 2) if a is not None and b is not None and b > 0 else None


def growth_z(series, lag):
    # Current lagged % change against prior lagged % changes in three years.
    changes = [growth(series[i], series[i-lag]) for i in range(lag, len(series))]
    if not changes or changes[-1] is None:
        return None
    prior = [v for v in changes[max(0, len(changes)-73):-1] if v is not None]
    if len(prior) < 12:
        return None
    sd = statistics.pstdev(prior)
    return round((changes[-1] - statistics.mean(prior)) / sd, 2) if sd > 0 else None


def dense(ticker, n):
    result = [None] * n
    for i, v in ticker.get('si', []):
        result[i] = v
    return result


def aggregate(series):
    return [sum(present) if (present := [v for v in values if v is not None]) else None
            for values in zip(*series)] if series else []


def constituent(old, raw, arrays):
    symbol = old['t']
    series = arrays.get(symbol, [None] * len(raw['dates']))
    result = dict(old)
    result.update(name=raw['tickers'].get(symbol, {}).get('name', old.get('name', symbol)),
                  latest=series[-1], ago6m=series[max(0, len(series)-14)],
                  chg=growth(series[-1], series[max(0, len(series)-14)]),
                  z6m=growth_z(series, 13), idx=indexed(series), spike=False)
    return result


def refresh_analytics(raw, sectors, insights):
    arrays = {symbol: dense(t, len(raw['dates'])) for symbol, t in raw['tickers'].items()}
    sectors['dates'] = list(raw['dates'])
    for symbol, sector in sectors.items():
        if symbol == 'dates':
            continue
        sector['constInfo'] = [constituent(c, raw, arrays) for c in sector['constInfo']]
        members = sector.get('constituents', [[]])[0]
        etf = arrays.get(symbol, [None] * len(raw['dates']))
        agg = aggregate([arrays.get(t, [None] * len(raw['dates'])) for t in members])
        sector.update(etfSI=etf, aggSI=agg, etfIdx=indexed(etf), aggIdx=indexed(agg),
                      etfLatest=etf[-1], etfAgo=etf[max(0, len(etf)-14)],
                      aggLatest=agg[-1], aggAgo=agg[max(0, len(agg)-14)])
    insights['dates'] = list(raw['dates'])
    candidates = []
    for symbol, t in raw['tickers'].items():
        series = arrays[symbol]
        if series[-1] is None:
            continue
        row = dict(t=symbol, name=t['name'], latest=series[-1],
                   ago6m=series[max(0,len(series)-14)], ago2w=series[max(0,len(series)-2)],
                   pct=dict(t.get('pct', [])).get(len(series)-1), spark=series[-26:])
        for label, lag in [('2w', 1), ('6w', 3), ('6m', 13)]:
            row['g'+label] = growth(series[-1], series[max(0,len(series)-1-lag)])
            row['z'+label] = growth_z(series, lag)
        candidates.append(row)
    for label in ['2w', '6w', '6m']:
        key = 'z' + label
        valid = [r for r in candidates if r[key] is not None]
        insights['rising_'+label] = sorted([r for r in valid if r[key] > 0], key=lambda r:r[key], reverse=True)[:150]
        insights['covering_'+label] = sorted([r for r in valid if r[key] < 0], key=lambda r:r[key])[:150]
    insights['rising'] = insights['rising_6m']
    insights['covering'] = insights['covering_6m']
    for theme in insights['themes']:
        theme['constInfo'] = [constituent(c, raw, arrays) for c in theme['constInfo']]
        theme['constSeries'] = [constituent(c, raw, arrays) for c in theme.get('constSeries', [])]
        series = [arrays.get(c['t'], [None]*len(raw['dates'])) for c in theme['constInfo']]
        theme['aggSI'] = aggregate(series)
        theme['aggIdx'] = indexed(theme['aggSI'])
        zs = [c['z6m'] for c in theme['constInfo'] if c['z6m'] is not None]
        theme['heat'] = round(statistics.mean(zs), 2) if zs else None
        theme['risingCount'] = sum(z >= 1.5 for z in zs)
        theme['totalCount'] = len(theme['constInfo'])
    return sectors, insights


def refresh_raw(template, dates, rows):
    old_dates = template['dates']
    raw = {'dates': dates, 'tickers': {}}
    for symbol, t in template['tickers'].items():
        raw['tickers'][symbol] = dict(t, si=[], pct=[])
    indices = {d: i for i,d in enumerate(dates)}
    old_si = {sym: dict(t.get('si', [])) for sym,t in template['tickers'].items()}
    old_pct = {sym: {old_dates[i]: (old_si[sym].get(i), v) for i,v in t.get('pct', [])}
               for sym,t in template['tickers'].items()}
    for settlement, symbol, name, quantity in rows:
        if symbol not in raw['tickers']:
            continue
        if quantity is None or quantity < 0:
            raise ValueError(f'Invalid SI for {symbol} on {settlement}')
        d = settlement.strftime('%Y%m%d')
        i = indices[d]
        ticker = raw['tickers'][symbol]
        ticker['si'].append([i, quantity])
        if name:
            ticker['name'] = html.escape(name, quote=True)
        # Same-date historical numerator corrections retain the published float.
        prior_si, prior_pct = old_pct[symbol].get(d, (None, None))
        if prior_si and prior_pct is not None:
            ticker['pct'].append([i, round(prior_pct * quantity / prior_si, 4)])
    if not any(t['si'] for t in raw['tickers'].values()):
        raise ValueError('Database snapshot contains no tracked ticker observations')
    return raw


def patch_dashboard(source, raw, manifest):
    template, _, _ = block(source, 'RAW')
    sectors = block(source, 'SECTOR_DATA')[0]
    insights = block(source, 'INSIGHTS_DATA')[0]
    sectors, insights = refresh_analytics(raw, sectors, insights)
    prices = remap_prices(block(source, 'PRICES')[0], template['dates'], raw['dates'])
    for name, data in [('RAW',raw), ('SECTOR_DATA',sectors), ('INSIGHTS_DATA',insights), ('PRICES',prices)]:
        source = replace_block(source, name, data)
    source, _ = patch_static_markup(source, raw['dates'])
    source = re.sub(r'Bi-weekly settlement data (?:·|&middot;) \d{4}(?:–|&ndash;)\d{4} (?:·|&middot;) [\d,]+ tickers',
                    f'Bi-weekly settlement data · {raw["dates"][0][:4]}–{raw["dates"][-1][:4]} · {len(raw["tickers"]):,} tickers', source)

    # Require actual observations at the chosen endpoints; avoid stale carry-forward.
    source = source.replace("for(let i=toIdx;i>=0;i--){if(pv[i]!==undefined){currentSIPct=pv[i];break;}}", "currentSIPct=pv[toIdx]??null;")
    source = source.replace("let fromVal=null;for(let i=fromIdx;i>=0;i--){if(vals[i]!==undefined){fromVal=vals[i];break;}}", "let fromVal=vals[fromIdx]??null;")
    source = source.replace("let toVal=null;for(let i=toIdx;i>=0;i--){if(vals[i]!==undefined){toVal=vals[i];break;}}", "let toVal=vals[toIdx]??null;")
    # Default to the fresh FINRA shares view; preserve historical float toggle.
    source = source.replace("currentView='pct'", "currentView='si'")
    source = source.replace("populatePeriodSelects();\nrenderChips();\nrenderChart();", "populatePeriodSelects();\nsetView('si');\nrenderChips();\nrenderChart();")
    source = source.replace('spanGaps:true', 'spanGaps:false')
    if "// FINRA_PUBLISH_MOVER_VIEW" not in source:
        # Movers show current FINRA shares by default, with historical float as a toggle.
        source = source.replace(' · SI % of Float";', ' · "+(minSIType==="shares"?"Shares Short":"SI % of Float");')
        source = source.replace('    var deltaPct = (curPct!==null && agoPct!==null) ? curPct - agoPct : null;',
            '    if(minSIType==="shares"){var siMap={}; (t.si||[]).forEach(function(p){siMap[p[0]]=p[1];}); curPct=r.latest; agoPct=siMap[agoIdx]??null;}\n'
            '    var deltaPct = (curPct!==null && agoPct!==null) ? curPct - agoPct : null;')
        source = source.replace('r.curPct.toFixed(2)+"%"', '(minSIType==="shares"?fmtSI2(r.curPct):r.curPct.toFixed(2)+"%")')
        source = source.replace('r.agoPct.toFixed(2)+"%"', '(minSIType==="shares"?fmtSI2(r.agoPct):r.agoPct.toFixed(2)+"%")')
        source = source.replace('r.deltaPct.toFixed(2)+"pp"', '(minSIType==="shares"?fmtSI2(r.deltaPct):r.deltaPct.toFixed(2)+"pp")')
        source = source.replace('var lookbackAgoLabel = lookback==="2w"?"2w Ago":"6mo Ago";',
            'var lookbackAgoLabel = lookback==="2w"?"2w Ago":lookback==="6w"?"6w Ago":"6mo Ago";')
        source = source.replace('    if(col==="agoPct") th.childNodes[0].textContent = lookbackAgoLabel+" ";',
            '    if(col==="agoPct") th.childNodes[0].textContent = lookbackAgoLabel+" ";\n'
            '    if(col==="curPct") th.childNodes[0].textContent = (minSIType==="shares"?"Current SI":"Current SI%")+" ";\n'
            '    if(col==="deltaPct") th.childNodes[0].textContent = (minSIType==="shares"?"Δ SI shares":"Δ SI%")+" ";')
        source = source.replace('window.renderRising = function(){',
                                '// FINRA_PUBLISH_MOVER_VIEW\nwindow.renderRising = function(){', 1)
    # Replace select markup instead of appending repeated initialization scripts.
    for select_id, value in [('metric','si'), ('th-yaxis','raw')]:
        pattern = r'(<select[^>]*id="' + select_id + r'"[^>]*>)(.*?)(</select>)'
        def default_option(match):
            options = re.sub(r'\s+selected(?:="selected")?', '', match.group(2))
            options = options.replace('value="'+value+'"', 'value="'+value+'" selected')
            return match.group(1)+options+match.group(3)
        source = re.sub(pattern, default_option, source, count=1, flags=re.S)
    source = re.sub(r'<!-- FINRA_FRESHNESS_START -->.*?<!-- FINRA_FRESHNESS_END -->\n?', '', source, flags=re.S)
    pct_dates = [raw['dates'][i] for t in raw['tickers'].values() for i,_ in t['pct']]
    price_dates = [raw['dates'][run[0]+i] for run in prices.values() for i,v in enumerate(run[1:]) if v is not None]
    asof = lambda d: f'{d[:4]}-{d[4:6]}-{d[6:]}'
    candidate_date = block(source, 'CANDIDATES')[0].get('asof', 'unavailable')
    note = (f'FINRA through {asof(raw["dates"][-1])} · {len(raw["dates"])} periods · '
            f'{len(raw["tickers"]):,} tracked tickers. Auto-refreshed from Supabase. '
            f'Float ratios through {asof(max(pct_dates)) if pct_dates else "unavailable"}; '
            f'prices through {asof(max(price_dates)) if price_dates else "unavailable"}; '
            f'Candidates snapshot {candidate_date}. New float ratios require a separate source refresh. '
            'Raw share counts are not split-adjusted. Basket membership and market caps use the existing snapshot. '
            'Mover z-scores compare raw SI percent changes with prior changes over three years; theme heat averages constituent 6-month z-scores.')
    banner = '<!-- FINRA_FRESHNESS_START --><div id="finra-freshness" style="padding:12px 24px;color:#a0aec0;background:#16213e;font-size:.8rem">'+html.escape(note)+'</div><!-- FINRA_FRESHNESS_END -->'
    source = source.replace('<div class="tabs">', banner+'\n<div class="tabs">', 1)
    return source


def main():
    import psycopg
    ap = argparse.ArgumentParser()
    ap.add_argument('--template', type=Path, default=ROOT/'si_dashboard.html')
    ap.add_argument('--output-dir', type=Path, default=ROOT/'site')
    args = ap.parse_args()
    source = args.template.read_text(encoding='utf-8')
    template = block(source, 'RAW')[0]
    url = os.environ.get('SUPABASE_DB_URL')
    if not url:
        raise RuntimeError('SUPABASE_DB_URL is required')
    # Manifest and rows must be read from the same snapshot while ingestion can run.
    with psycopg.connect(url, sslmode='require', connect_timeout=15) as conn:
        conn.execute('set transaction isolation level repeatable read read only')
        manifest = conn.execute('select settlement_date, row_count from public.finra_ingest_runs order by settlement_date').fetchall()
        if not manifest:
            raise ValueError('No ingested periods; run backfill first')
        dates = [d.strftime('%Y%m%d') for d,_ in manifest]
        counts = {d: n for d,n in manifest}
        actual = dict(conn.execute('select settlement_date, count(*) from public.finra_short_interest group by settlement_date').fetchall())
        if counts != actual:
            raise ValueError('Database row counts differ from committed ingestion manifest')
        if not set(template['dates']).issubset(dates):
            raise ValueError('Database is missing periods already published in the dashboard')
        with conn.cursor(name='dashboard_rows') as cur:
            cur.itersize = 10000
            cur.execute('select settlement_date, symbol, issue_name, current_short from public.finra_short_interest where symbol = any(%s) order by settlement_date, symbol', (list(template['tickers']),))
            raw = refresh_raw(template, dates, cur)
    output = patch_dashboard(source, raw, manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir/'si_dashboard.html'
    target.write_text(output, encoding='utf-8')
    subprocess.run([sys.executable, str(ROOT/'validate_dashboard.py'), str(target)], check=True)
    # Keep the historical URL and offer a convenient root landing URL.
    (args.output_dir/'index.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=si_dashboard.html"><title>FINRA Short Interest Tracker</title><a href="si_dashboard.html">Open dashboard</a>', encoding='utf-8')
    version = hashlib.sha256(output.encode()).hexdigest()
    (args.output_dir/'version.json').write_text(json.dumps({'version':version, 'settlement_date':dates[-1]}))
    print(f'Validated dashboard: {len(raw["tickers"]):,} tickers, {len(dates)} periods through {dates[-1]}')


if __name__ == '__main__':
    main()
