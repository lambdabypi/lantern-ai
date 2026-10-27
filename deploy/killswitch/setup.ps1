# One-time setup of the budget kill switch (see main.py). Run from PowerShell in the repo root:
#     .\deploy\killswitch\setup.ps1
# What it creates in project lantern-ai-85172 (all inside the free tier):
#   - APIs: Cloud Functions, Eventarc
#   - Pub/Sub topic lantern-budget, which the new budget publishes to several times a day
#   - service account lantern-killswitch, allowed to manage ONLY the lantern Cloud Run service (roles/run.admin on it)
#   - Cloud Run function lantern-killswitch (not public, at most 1 instance), triggered by the topic
#   - budget "Lantern kill switch 5 USD" for this project only, wired to the topic
# When this month's bill for the project reaches $KillAt, the function removes public access to the app.
# Turn the app back on:
#     gcloud run services add-iam-policy-binding lantern --region us-central1 --member allUsers --role roles/run.invoker
$ErrorActionPreference = "Stop"
$env:CLOUDSDK_ACTIVE_CONFIG_NAME = "lantern"
$Project = "lantern-ai-85172"
$Region = "us-central1"
$KillAt = 5
$SA = "lantern-killswitch@$Project.iam.gserviceaccount.com"

gcloud services enable cloudfunctions.googleapis.com eventarc.googleapis.com
gcloud pubsub topics create lantern-budget
gcloud iam service-accounts create lantern-killswitch --display-name "Lantern budget kill switch"
gcloud run services add-iam-policy-binding lantern --region $Region --member "serviceAccount:$SA" --role roles/run.admin

gcloud functions deploy lantern-killswitch --gen2 --runtime python312 --region $Region --source $PSScriptRoot `
    --entry-point stop --trigger-topic lantern-budget --service-account $SA --trigger-service-account $SA `
    --no-allow-unauthenticated --max-instances 1 --memory 256Mi `
    --set-env-vars "SERVICE=projects/$Project/locations/$Region/services/lantern,KILL_AT_USD=$KillAt"
gcloud run services add-iam-policy-binding lantern-killswitch --region $Region --member "serviceAccount:$SA" --role roles/run.invoker

$Billing = (gcloud billing projects describe $Project --format "value(billingAccountName)") -replace "billingAccounts/", ""
$Number = gcloud projects describe $Project --format "value(projectNumber)"
gcloud billing budgets create --billing-account $Billing --display-name "Lantern kill switch $KillAt USD" `
    --budget-amount "$($KillAt)USD" --filter-projects "projects/$Number" --threshold-rule percent=1.0 `
    --notifications-rule-pubsub-topic "projects/$Project/topics/lantern-budget"

Write-Host "Done. Test it without turning anything off (it should log 'nothing to do'):"
# (Windows PowerShell 5.1 drops bare inner quotes when calling gcloud, hence \" inside single quotes)
Write-Host '  gcloud pubsub topics publish lantern-budget --message ''{\"costAmount\": 0.01, \"budgetAmount\": 5}'''
Write-Host "  gcloud functions logs read lantern-killswitch --region $Region --limit 5"
