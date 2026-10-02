#!/usr/bin/env python3
"""Fetch FINRA bi-weekly short interest files into Supabase Postgres.

Usage:
    python ingest_finra_supabase.py              # probe recent weekdays, load anything new
    python ingest_finra_supabase.py --backfill   # also load every date in SETTLEMENT_DATES
    python ingest_finra_supabase.py --dry-run    # fetch + parse, don't touch the database

Env:
    SUPABASE_DB_URL   Postgres connection string (Supabase > Project Settings > Database).
                      Use the session-mode pooler or direct connection, not transaction mode.

Idempotent: each period is loaded in one transaction, upserted on
(settlement_date, symbol), so reruns and FINRA revisions are safe. Recent periods are refreshed on every run;
use --refresh with --backfill to reload older revisions.
"""

import argparse
import io
import os
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import pandas as pd
import requests

from fetch_short_interest import BASE_URL, SETTLEMENT_DATES

PROBE_DAYS = 45  # FINRA publishes ~8 business days after settlement

COLUMNS = {
    "settlementDate": "settlement_date",
    "symbolCode": "symbol",
    "issueName": "issue_name",
    "issuerServicesGroupExchangeCode": "exchange_code",
    "marketClassCode": "market_class",
    "currentShortPositionQuantity": "current_short",
    "previousShortPositionQuantity": "previous_short",
    "stockSplitFlag": "stock_split_flag",
    "averageDailyVolumeQuantity": "avg_daily_volume",
    "daysToCoverQuantity": "days_to_cover",
    "revisionFlag": "revision_flag",
    "changePercent": "change_pct",
    "changePreviousNumber": "change_prev",
}
INT_COLS = ["current_short", "previous_short", "avg_daily_volume", "change_prev"]
NUM_COLS = ["days_to_cover", "change_pct"]


def candidate_dates(backfill: bool) -> list[str]:
    today = date.today()
    recent = [today - timedelta(days=n) for n in range(PROBE_DAYS)]
    cands = {d.strftime("%Y%m%d") for d in recent if d.weekday() < 5}
    if backfill:
        cands |= set(SETTLEMENT_DATES)
    return sorted(d for d in cands if d <= today.strftime("%Y%m%d"))


def http_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )))
    return session


def parse_period(text: str, date_str: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str, keep_default_na=False)
    missing = set(COLUMNS) - set(df.columns)
    if missing or df.empty:
        raise ValueError(f"{date_str}: empty file or missing columns: {sorted(missing)}")
    expected = pd.to_datetime(date_str, format="%Y%m%d").date()
    dates = pd.to_datetime(df["settlementDate"].str.strip().str.replace("-", "", regex=False), format="%Y%m%d", errors="raise")
    if not (dates.dt.date == expected).all():
        raise ValueError(f"{date_str}: file contains a different settlement date")
    df = df[list(COLUMNS)].rename(columns=COLUMNS)
    df["settlement_date"] = expected
    for c in df.columns:
        if c != "settlement_date":
            df[c] = df[c].str.strip()
    if (df["symbol"] == "").any() or df["symbol"].duplicated().any():
        raise ValueError(f"{date_str}: blank or duplicate symbols")
    for c in INT_COLS + NUM_COLS:
        values = []
        for value in df[c]:
            if value == "":
                values.append(None)
                continue
            try:
                number = Decimal(value)
            except InvalidOperation:
                raise ValueError(f"{date_str}: invalid {c} value") from None
            if not number.is_finite():
                raise ValueError(f"{date_str}: non-finite {c} value")
            if c in INT_COLS:
                if number != number.to_integral_value() or not -(2**63) <= number < 2**63:
                    raise ValueError(f"{date_str}: invalid bigint {c}")
                number = int(number)
            values.append(number)
        df[c] = pd.Series(values, dtype=object)
    if df["current_short"].isna().any():
        raise ValueError(f"{date_str}: missing current short interest")
    return df


def fetch_period(date_str: str, session=None) -> pd.DataFrame | None:
    r = (session or requests).get(BASE_URL.format(date=date_str), timeout=(10, 60))
    if r.status_code in (403, 404):  # CDN answers 403 for unpublished files
        return None
    r.raise_for_status()
    return parse_period(r.text, date_str)


def load_period(conn, df: pd.DataFrame) -> int:
    if df.empty or df["settlement_date"].nunique() != 1:
        raise ValueError("load_period requires one nonempty settlement period")
    cols = list(COLUMNS.values())
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("settlement_date", "symbol"))
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(%s, %s)",
                    (4650, int(df["settlement_date"].iloc[0].strftime("%Y%m%d"))))
        cur.execute(f"create temp table _stage (like public.finra_short_interest including defaults) on commit drop")
        with cur.copy(f"copy _stage ({col_list}) from stdin with (format csv, null '')") as cp:
            while chunk := buf.read(1 << 20):
                cp.write(chunk)
        # FINRA files are complete snapshots: remove symbols omitted by a revision.
        cur.execute("delete from public.finra_short_interest where settlement_date = %s "
                    "and symbol not in (select symbol from _stage)",
                    (df["settlement_date"].iloc[0],))
        cur.execute(
            f"insert into public.finra_short_interest ({col_list}) select {col_list} from _stage "
            f"on conflict (settlement_date, symbol) do update set {updates}, ingested_at = now()"
        )
        cur.execute(
            "insert into public.finra_ingest_runs (settlement_date, row_count) values (%s, %s) "
            "on conflict (settlement_date) do update set row_count = excluded.row_count, ingested_at = now()",
            (df["settlement_date"].iloc[0], len(df)),
        )
    return len(df)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="Reload already loaded historical periods")
    args = ap.parse_args()

    conn, done = None, set()
    if not args.dry_run:
        import psycopg

        url = os.environ.get("SUPABASE_DB_URL")
        if not url:
            print("SUPABASE_DB_URL is not set", file=sys.stderr)
            return 2
        try:
            conn = psycopg.connect(url, autocommit=True, connect_timeout=15, sslmode="require")
            with conn.cursor() as cur:
                cur.execute("select settlement_date from public.finra_ingest_runs")
                done = {r[0].strftime("%Y%m%d") for r in cur.fetchall()}
        except Exception as exc:
            print(f"Database initialization failed ({type(exc).__name__}); "
                  "check the connection secret and apply the migrations", file=sys.stderr)
            if conn:
                conn.close()
            return 2

    loaded = 0
    failures = 0
    recent = set(candidate_dates(False))
    try:
        with http_session() as session:
            for d in candidate_dates(args.backfill):
                if d in done and d not in recent and not args.refresh:
                    continue
                try:
                    df = fetch_period(d, session)
                    if df is None:
                        if d in done:
                            raise ValueError("previously loaded file is unavailable")
                        continue
                    n = len(df) if args.dry_run else load_period(conn, df)
                    print(f"  {d}: {n:,} rows{' (dry run)' if args.dry_run else ''}")
                    loaded += 1
                except Exception as exc:
                    # Do not print connection URLs or credentials in CI logs.
                    detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    print(f"  {d}: failed ({detail})", file=sys.stderr)
                    failures += 1
        print(f"Done. {loaded} period(s) processed; {failures} failed.")
    finally:
        if conn:
            conn.close()
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
