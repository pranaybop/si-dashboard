#!/bin/sh
# Create two Cloud Run jobs and two Cloud Scheduler triggers.
# Requires gcloud auth, a project, and a Secret Manager secret named SUPABASE_DB_URL
# whose value is the session-pooler URI. This script prints no secret values.
#
#   export PROJECT=my-project REGION=us-central1
#   # repo that should rebuild Pages; token is a Contents: write fine-grained PAT
#   export GITHUB_REPOSITORY=zyyzx/si-dashboard
#   export GITHUB_DISPATCH_TOKEN=...   # optional; omit to skip Pages dispatch
#   sh scheduler/cloud-run/setup.sh
set -eu

: "${PROJECT:?set PROJECT}"
: "${REGION:?set REGION}"

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com

if ! gcloud secrets describe SUPABASE_DB_URL >/dev/null 2>&1; then
  echo "Create the secret first, for example:" >&2
  echo "  gcloud secrets create SUPABASE_DB_URL --replication-policy=automatic" >&2
  echo "  gcloud secrets versions add SUPABASE_DB_URL --data-file=-   # paste URI, then Ctrl-D" >&2
  exit 1
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUN_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding SUPABASE_DB_URL \
  --member="serviceAccount:${RUN_SA}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"

SECRETS="SUPABASE_DB_URL=SUPABASE_DB_URL:latest"
ENV_VARS=""
if [ -n "${GITHUB_DISPATCH_TOKEN:-}" ]; then
  if ! gcloud secrets describe GITHUB_DISPATCH_TOKEN >/dev/null 2>&1; then
    printf '%s' "$GITHUB_DISPATCH_TOKEN" | gcloud secrets create GITHUB_DISPATCH_TOKEN --data-file=-
    gcloud secrets add-iam-policy-binding GITHUB_DISPATCH_TOKEN \
      --member="serviceAccount:${RUN_SA}" \
      --role="roles/secretmanager.secretAccessor" >/dev/null
  fi
  SECRETS="${SECRETS},GITHUB_DISPATCH_TOKEN=GITHUB_DISPATCH_TOKEN:latest"
  ENV_VARS="GITHUB_REPOSITORY=${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY when GITHUB_DISPATCH_TOKEN is set}"
fi

deploy_job() {
  name="$1"
  shift
  # shellcheck disable=SC2086
  gcloud run jobs deploy "$name" \
    --source "$ROOT" \
    --region "$REGION" \
    --service-account "$RUN_SA" \
    --set-secrets "$SECRETS" \
    ${ENV_VARS:+--set-env-vars "$ENV_VARS"} \
    --task-timeout 3600 \
    --max-retries 1 \
    --memory 1Gi \
    --cpu 1 \
    "$@"
}

deploy_job finra-ingest --args=refresh-cycle
deploy_job finra-freshness --args=check

SCHED_SA="finra-scheduler@${PROJECT}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$SCHED_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create finra-scheduler --display-name="FINRA scheduler"
create_schedule() {
  name="$1"
  cron="$2"
  job="$3"
  uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${job}:run"
  gcloud run jobs add-iam-policy-binding "$job" \
    --region "$REGION" \
    --member="serviceAccount:${SCHED_SA}" \
    --role="roles/run.invoker" >/dev/null
  common="--location $REGION --schedule $cron --time-zone America/Chicago --uri $uri --http-method POST --message-body {} --headers Content-Type=application/json --oauth-service-account-email $SCHED_SA --oauth-token-scope https://www.googleapis.com/auth/cloud-platform"
  if gcloud scheduler jobs describe "$name" --location "$REGION" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    gcloud scheduler jobs update http "$name" $common
  else
    # shellcheck disable=SC2086
    gcloud scheduler jobs create http "$name" $common
  fi
}

create_schedule finra-ingest-weekday "17 6 * * 1-5" finra-ingest
create_schedule finra-freshness-weekday "45 7 * * 1-5" finra-freshness

echo "Cloud Scheduler will run finra-ingest at 06:17 and finra-freshness at 07:45 America/Chicago, weekdays."
