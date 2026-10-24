# Deploy (or redeploy) Lantern to Google Cloud Run.
#
# One-time setup, already done for project lantern-ai-85172 (see deploy/README.md):
#   project + billing + $1 budget, APIs enabled, bucket <project>-data, service account lantern-run,
#   secrets token-factory-api-key / tavily-api-key / app-secret-key / vapid-private-key / cron-secret in Secret Manager.
#   The daily recall check is a Cloud Scheduler job (see deploy/README.md).
#
# Usage (PowerShell, from the repo root):
#   $env:CLOUDSDK_ACTIVE_CONFIG_NAME = "lantern"   # gcloud configuration using the personal account
#   .\deploy\cloudrun.ps1
# Non-secret settings (base URL, model IDs) are read from .env; secrets come from Secret Manager.

param(
    [string]$Project = "lantern-ai-85172",
    [string]$Region = "us-central1",
    [string]$PublicUrl = "https://lantern-976584717995.us-central1.run.app"
)
$ErrorActionPreference = "Stop"
$sa = "lantern-run@$Project.iam.gserviceaccount.com"

$envs = & ".venv\Scripts\python.exe" -c "from app.config import load_settings; s=load_settings('.env'); print(','.join([f'TOKEN_FACTORY_BASE_URL={s.token_factory_base_url}', f'MODEL_FAST={s.model_fast}', f'MODEL_BALANCED={s.model_balanced}', f'MODEL_REASONING={s.model_reasoning}', 'DATA_DIR=/data', 'CACHE_ENABLED=false', 'COOKIE_SECURE=true', 'GUEST_TTL_DAYS=7', 'GUEST_ULTRA_PER_DAY=10', 'GUEST_MESSAGES_PER_DAY=30', 'USER_MESSAGES_PER_DAY=100', 'SITE_MESSAGES_PER_DAY=300', 'SITE_TAVILY_CREDITS_PER_DAY=60', 'GUESTS_PER_IP_PER_DAY=30', 'PUBLIC_BASE_URL=$PublicUrl']))"

$deployArgs = @(
    "run", "deploy", "lantern", "--project", $Project, "--source", ".", "--region", $Region, "--quiet",
    "--service-account", $sa, "--allow-unauthenticated",
    # One instance at most: user data is files in the bucket, so a single writer keeps them consistent.
    # Zero when idle: no cost between visits (the first visit after idle has a cold start of ~10 s).
    "--min-instances", "0", "--max-instances", "1", "--memory", "512Mi", "--cpu", "1",
    "--timeout", "300", "--concurrency", "20", "--execution-environment", "gen2",
    "--set-env-vars", $envs,
    "--set-secrets", "TOKEN_FACTORY_API_KEY=token-factory-api-key:latest,TAVILY_API_KEY=tavily-api-key:latest,APP_SECRET_KEY=app-secret-key:latest,VAPID_PRIVATE_KEY=vapid-private-key:latest,CRON_SECRET=cron-secret:latest",
    # The container runs as uid 10001, so the bucket is mounted with that owner.
    "--add-volume", "name=data,type=cloud-storage,bucket=$Project-data,mount-options=uid=10001;gid=10001",
    "--add-volume-mount", "volume=data,mount-path=/data"
)
# gcloud writes progress to stderr, which Windows PowerShell 5.1 turns into errors; judge by the exit code instead.
$ErrorActionPreference = "Continue"
& gcloud @deployArgs 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { throw "gcloud run deploy failed (exit code $LASTEXITCODE)" }
