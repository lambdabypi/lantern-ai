# Deploying Lantern

**Live demo:** https://lantern-976584717995.us-central1.run.app (Google Cloud Run, us-central1)

All AI inference runs on **Nebius Token Factory** (Nemotron Nano, Super and Ultra); Cloud Run only hosts the web app.

## How it runs

| Piece | Setting |
|---|---|
| Hosting | Cloud Run, gen2, 1 vCPU / 512 MiB, **0–1 instances**: free when idle; one writer keeps file-based data consistent |
| User data | Cloud Storage bucket `lantern-ai-85172-data`, mounted at `/data` (`DATA_DIR=/data`). Private: public access prevention on |
| Secrets | Secret Manager: `token-factory-api-key`, `tavily-api-key`, `steam-api-key`, `app-secret-key`. Readable only by the `lantern-run` service account |
| Settings | `CACHE_ENABLED=false`, `COOKIE_SECURE=true`, `PUBLIC_BASE_URL` = the run.app URL (Steam sign-in return address) |
| Cost guard | Budget "Lantern - 1 USD" on the project, emails at 50 / 90 / 100 % |

Expect a cold start of about 10 s on the first visit after an idle period.

## Redeploy

```powershell
$env:CLOUDSDK_ACTIVE_CONFIG_NAME = "lantern"
.\deploy\cloudrun.ps1
```

Use PowerShell: Git Bash rewrites `/data` into a Windows path.

## Rotate a secret

```powershell
"new-value" | gcloud secrets versions add tavily-api-key --data-file=-
.\deploy\cloudrun.ps1    # picks up :latest
```

## One-time setup (already done)

1. Project `lantern-ai-85172` with billing linked, plus the $1 budget.
2. APIs enabled: run, cloudbuild, artifactregistry, secretmanager, storage, iam, billingbudgets.
3. Bucket `gs://lantern-ai-85172-data` (us-central1, uniform access, public access prevention).
4. Service account `lantern-run` with `roles/storage.objectUser` on the bucket and `roles/secretmanager.secretAccessor` on each secret.
5. Secrets created from `.env`, except a freshly generated `app-secret-key` for production.
