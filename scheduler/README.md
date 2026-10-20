# Weekday FINRA ingest outside GitHub Actions

The image in the repo root runs the same Python the Actions workflow runs.
`SUPABASE_DB_URL` is read from the environment at start. Use the Supabase
**session pooler** URI (port 5432). Direct connections usually need IPv6.

```sh
docker build -t finra-ingest:local .
docker run --rm --env-file /path/finra.env finra-ingest:local                 # recent probe
docker run --rm --env-file /path/finra.env finra-ingest:local --backfill      # every known date
docker run --rm --env-file /path/finra.env finra-ingest:local --backfill --refresh
docker run --rm --env-file /path/finra.env finra-ingest:local --since-last    # gap since newest load
docker run --rm --env-file /path/finra.env finra-ingest:local check           # exit 1 when stale
docker run --rm --env-file /path/finra.env finra-ingest:local refresh-cycle   # ingest, check, dispatch Pages
```

The same commands work without Docker from a checkout with `requirements-finra.txt`
installed: `python ingest_finra_supabase.py`, `python check_finra_freshness.py`.

## Freshness

`check_finra_freshness.py` reads `public.finra_ingest_runs`:

- `max(ingested_at)` must be at least as new as the latest weekday 06:17
  America/Chicago whose 75-minute grace has elapsed. A run that is still inside
  the grace window is judged against the previous weekday, so a Monday 07:00
  check still accepts Friday's load.
- `max(settlement_date)` must be within 36 days. FINRA publishes about every
  two weeks, so this fires when a whole settlement file was missed, including
  on days the ingest heartbeat itself is new.

Exit 0 prints `OK:`. Exit 1 prints one or more `STALE:` lines. Exit 2 means the
URI is missing or the connection failed. The URI is not written to the output.

Overrides: `--timezone`, `--schedule-time`, `--grace-minutes`,
`--max-settlement-age-days`, or the `FINRA_*` variables of the same names.

A daily success rewrites `ingested_at` on recent periods even when FINRA
publishes nothing that morning. A skipped run leaves `ingested_at` on the
previous weekday and the 07:45 check fails.

## Fill a gap

```sh
python ingest_finra_supabase.py --since-last
```

Requires rows already in `finra_ingest_runs`. It probes weekdays from the newest
loaded settlement through today, plus any date in `SETTLEMENT_DATES` that never
loaded. Dates already loaded outside the recent 45-day window are left alone.
An empty database should use `--backfill` instead. Corrections older than 45
days still use `--backfill --refresh`. Extend `SETTLEMENT_DATES` in
`fetch_short_interest.py` when a new year starts; the forward weekday probe
still finds a file newer than the newest loaded settlement.

## Pages after an external ingest

`refresh-cycle` dispatches GitHub Actions when both of these are set:

- `GITHUB_REPOSITORY` — `owner/name` of the repo whose Pages site should update
- `GITHUB_DISPATCH_TOKEN` — fine-grained PAT with **Contents: Read and write**
  on that repo (classic PAT: `repo` scope). Actions: write is a different API
  and does not authorize `repository_dispatch`.

The POST sends `{"event_type":"finra-publish"}`. The workflow on the default
branch runs `publish_finra_dashboard.py` and deploys Pages. It does not ingest
again. Manual `workflow_dispatch` still runs ingest and then Pages.

```sh
curl -fsS -X POST \
  -H "Authorization: Bearer $GITHUB_DISPATCH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/dispatches" \
  -d '{"event_type":"finra-publish"}'
```

## systemd on a VPS

Needs Docker and systemd 247 or newer (`Timezone=` in a timer unit).

```sh
sudo docker build -t finra-ingest:local /opt/si-dashboard
sudo install -m 600 scheduler/systemd/finra-ingest.env.example /etc/finra-ingest.env
sudo editor /etc/finra-ingest.env
sudo cp scheduler/systemd/finra-ingest.service scheduler/systemd/finra-ingest.timer \
        scheduler/systemd/finra-freshness.service scheduler/systemd/finra-freshness.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now finra-ingest.timer finra-freshness.timer
sudo systemctl start finra-ingest.service
systemctl list-timers 'finra-*'
journalctl -u finra-ingest.service -u finra-freshness.service
```

`Persistent=true` runs a timer that was missed because the machine was off.
The ingest unit is the morning load (`refresh-cycle`). The freshness unit at
07:45 America/Chicago runs `check` only, so a morning run that never started
still fails in the journal.

## Airflow

Compose file: `scheduler/airflow/docker-compose.yml`. It is Airflow 3.3.2 with
LocalExecutor, Postgres, the API server, the scheduler, and the DAG processor.
No Redis or Celery. Plan on about 2 GB of free memory; the upstream quick start
asks for 4 GB.

```sh
cd scheduler/airflow
cp .env.example .env
# fill FERNET_KEY and SUPABASE_DB_URL in .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
docker compose up airflow-init
docker compose up -d
```

UI: `http://localhost:8080` with the username and password from `.env`
(defaults `airflow` / `airflow`). Change those before the port is reachable
from anywhere but localhost.

DAGs, unpaused in the UI:

- `finra_short_interest_to_supabase` — weekdays 06:17 America/Chicago. Tasks:
  ingest (2 retries, 15 minutes apart, 55 minute limit), freshness check,
  Pages dispatch. A Deadline Alert fires if the run is still unfinished 75
  minutes after its logical time. Deadline Alerts are experimental in Airflow 3.
- `finra_short_interest_freshness` — weekdays 07:45 America/Chicago. Runs the
  freshness check only, so a paused ingest DAG is still visible.

`catchup` is off. Trigger `finra_short_interest_to_supabase` once by hand after
the first `airflow-init` to confirm the Supabase URI.

## Cloud Scheduler and Cloud Run

`scheduler/cloud-run/setup.sh` enables Run, Scheduler, and Secret Manager,
builds this repo's Dockerfile as two jobs (`finra-ingest` args `refresh-cycle`,
`finra-freshness` args `check`), and creates weekday HTTP schedules in
`America/Chicago`.

```sh
gcloud secrets create SUPABASE_DB_URL --replication-policy=automatic
gcloud secrets versions add SUPABASE_DB_URL --data-file=-   # paste the URI, then Ctrl-D
export PROJECT=your-project REGION=us-central1
export GITHUB_REPOSITORY=owner/repo          # optional, with the token below
export GITHUB_DISPATCH_TOKEN=...              # optional Contents: write PAT
sh scheduler/cloud-run/setup.sh
```

The script does not print secret values. The runtime service account is the
project's default compute account; your user needs permission to deploy as that
account, and Cloud Build must be allowed to build the source. The scheduler
account `finra-scheduler@PROJECT` receives `roles/run.invoker` on the two jobs.

If a scheduler execution returns 404, the Cloud Run jobs API path has moved.
In `setup.sh`, switch the URI to:

```text
https://run.googleapis.com/v2/projects/PROJECT/locations/REGION/jobs/JOB:run
```

Pricing checked against the Cloud Scheduler and Cloud Run price pages: the
first three Scheduler jobs on a billing account are free, and Cloud Run jobs
include 240,000 vCPU-seconds and 450,000 GiB-seconds per month in `us-central1`.
A one-vCPU weekday job that runs a few minutes stays inside that tier.

## Attempt log

`supabase/migrations/0003_finra_ingest_job_log.sql` adds
`public.finra_ingest_job_log` (`started_at`, `finished_at`, `status`,
`row_count`, `settlement_date`, `detail`). Apply it in the Supabase SQL editor
when you want that history. Row level security is on and `anon` / `authenticated`
have no privileges. Ingest records one row per file it actually loads or fails,
and ignores a missing table. Unpublished 404/403 probes are not logged.

## Switch-over

1. Confirm `docker run ... check` or `python check_finra_freshness.py` prints `OK:` after a manual load.
2. Enable the timer, the two DAGs, or the two Scheduler jobs.
3. Set the dispatch token only on the repo that should publish Pages.
4. Delete the `schedule:` block in `.github/workflows/finra_fetch.yml` after the first successful external weekday. Keep `workflow_dispatch` and `repository_dispatch`.
