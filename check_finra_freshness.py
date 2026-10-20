#!/usr/bin/env python3
"""Exit non-zero when the FINRA Supabase load is late or a settlement was missed.

The weekday ingest is expected at 06:17 America/Chicago. After the grace period
this check fails unless public.finra_ingest_runs.ingested_at is at least that
recent. It also fails when the newest settlement_date is older than the
biweekly publication window. The script only reads the database.

A successful daily ingest refreshes recent periods, so ingested_at moves even
on days when FINRA publishes nothing new. Settlement age is the separate
"a file never arrived" signal. FINRA publishes about every two weeks, roughly
eight business days after settlement, so a healthy newest settlement can be
several weeks old.

Env:
    SUPABASE_DB_URL                 Postgres URI (session pooler, TLS). Not printed.
    FINRA_TIMEZONE                  default America/Chicago
    FINRA_SCHEDULE_TIME             default 06:17 (24-hour, in FINRA_TIMEZONE)
    FINRA_GRACE_MINUTES             default 75
    FINRA_MAX_SETTLEMENT_AGE_DAYS   default 36
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Chicago"
DEFAULT_SCHEDULE = "06:17"
DEFAULT_GRACE_MINUTES = 75
DEFAULT_MAX_SETTLEMENT_AGE_DAYS = 36
SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class FreshnessStatus:
    ok: bool
    lines: tuple[str, ...]


def parse_schedule(value: str) -> time:
    hour_text, minute_text = value.split(":")
    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(value)
    return time(hour, minute)


def required_slot(now: datetime, schedule: time, tz: ZoneInfo, grace: timedelta) -> datetime:
    """Most recent weekday schedule time whose grace period has already elapsed."""
    local_now = now.astimezone(tz)
    day = local_now.date()
    for _ in range(14):
        if day.weekday() < 5:
            slot = datetime.combine(day, schedule, tzinfo=tz)
            if slot + grace <= local_now:
                return slot
        day -= timedelta(days=1)
    raise RuntimeError("no weekday schedule slot before now")


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC"))
    return value


def _fmt(value: datetime, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def assess(
    now: datetime,
    last_ingested_at: datetime | None,
    latest_settlement: date | None,
    *,
    schedule: time,
    tz: ZoneInfo,
    grace: timedelta,
    max_settlement_age_days: int,
) -> FreshnessStatus:
    if last_ingested_at is None or latest_settlement is None:
        return FreshnessStatus(False, (
            "STALE: public.finra_ingest_runs has no completed load. "
            "Run: python ingest_finra_supabase.py --backfill",
        ))

    problems: list[str] = []
    slot = required_slot(now, schedule, tz, grace)
    ingested = _as_aware(last_ingested_at)
    if ingested < slot - SKEW:
        problems.append(
            f"STALE: last successful ingest {_fmt(ingested, tz)} is before the required "
            f"weekday run at {_fmt(slot, tz)} (now {_fmt(now, tz)}, "
            f"grace {int(grace.total_seconds() // 60)} minutes, timezone {tz.key})."
        )
    age_days = (now.astimezone(tz).date() - latest_settlement).days
    if age_days > max_settlement_age_days:
        problems.append(
            f"STALE: latest settlement {latest_settlement.isoformat()} is {age_days} days old, "
            f"past the {max_settlement_age_days}-day window. FINRA publishes about every two weeks; "
            "a settlement file is missing."
        )
    if problems:
        return FreshnessStatus(False, tuple(problems))
    return FreshnessStatus(True, (
        f"OK: last ingest {_fmt(ingested, tz)}; "
        f"latest settlement {latest_settlement.isoformat()} ({age_days} days old).",
    ))


def fetch_status(conn) -> tuple[datetime | None, date | None]:
    with conn.cursor() as cur:
        cur.execute(
            "select max(ingested_at), max(settlement_date) from public.finra_ingest_runs"
        )
        row = cur.fetchone()
    return row[0], row[1]


def current_time() -> datetime:
    return datetime.now(ZoneInfo("UTC"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when the FINRA Supabase load is stale.")
    parser.add_argument("--timezone", default=os.environ.get("FINRA_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--schedule-time", default=os.environ.get("FINRA_SCHEDULE_TIME", DEFAULT_SCHEDULE))
    parser.add_argument(
        "--grace-minutes",
        type=int,
        default=int(os.environ.get("FINRA_GRACE_MINUTES", DEFAULT_GRACE_MINUTES)),
    )
    parser.add_argument(
        "--max-settlement-age-days",
        type=int,
        default=int(os.environ.get("FINRA_MAX_SETTLEMENT_AGE_DAYS", DEFAULT_MAX_SETTLEMENT_AGE_DAYS)),
    )
    args = parser.parse_args(argv)
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        print("SUPABASE_DB_URL is not set", file=sys.stderr)
        return 2
    if args.grace_minutes < 0 or args.max_settlement_age_days < 1:
        print("grace and settlement-age window must be positive", file=sys.stderr)
        return 2
    try:
        tz = ZoneInfo(args.timezone)
        schedule = parse_schedule(args.schedule_time)
        grace = timedelta(minutes=args.grace_minutes)
    except Exception:
        print("Invalid freshness settings", file=sys.stderr)
        return 2

    import psycopg

    try:
        conn = psycopg.connect(url, autocommit=True, connect_timeout=15, sslmode="require")
        try:
            last_ingested_at, latest_settlement = fetch_status(conn)
        finally:
            conn.close()
    except Exception:
        print(
            "Database connection failed; check SUPABASE_DB_URL and that the FINRA migrations are applied",
            file=sys.stderr,
        )
        return 2

    status = assess(
        current_time(),
        last_ingested_at,
        latest_settlement,
        schedule=schedule,
        tz=tz,
        grace=grace,
        max_settlement_age_days=args.max_settlement_age_days,
    )
    stream = sys.stdout if status.ok else sys.stderr
    for line in status.lines:
        print(line, file=stream)
    return 0 if status.ok else 1


if __name__ == "__main__":
    sys.exit(main())
