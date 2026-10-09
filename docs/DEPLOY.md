# Deploying to a Google Cloud VM

A small always-on VM is the simplest way to run ScholarPilot continuously (the email watcher needs a running process, and SQLite needs a persistent disk). Expect roughly €15 a month for an `e2-small` plus AI usage.

You need: a Google Cloud project with billing, the [gcloud CLI](https://cloud.google.com/sdk), and a domain name (a free one such as `<ip-with-dashes>.sslip.io` or DuckDNS works).

## 1 · Project, APIs and identity

```bash
PROJECT=your-project-id
gcloud config set project $PROJECT
gcloud services enable compute.googleapis.com aiplatform.googleapis.com gmail.googleapis.com

gcloud iam service-accounts create scholarpilot-vm
for role in roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:scholarpilot-vm@$PROJECT.iam.gserviceaccount.com" --role=$role
done
```

The VM talks to Vertex AI with this service account, so no API keys are needed.

## 2 · Network and VM

```bash
REGION=us-central1; ZONE=us-central1-a
gcloud compute addresses create scholarpilot-ip --region=$REGION
IP=$(gcloud compute addresses describe scholarpilot-ip --region=$REGION --format='value(address)')
gcloud compute firewall-rules create scholarpilot-web --allow=tcp:80,tcp:443 --target-tags=sp-web

gcloud compute instances create scholarpilot --zone=$ZONE --machine-type=e2-small \
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=20GB \
  --address=scholarpilot-ip --tags=sp-web \
  --service-account=scholarpilot-vm@$PROJECT.iam.gserviceaccount.com --scopes=cloud-platform \
  --metadata=enable-oslogin=TRUE \
  --metadata-from-file=startup-script=<(printf '#!/bin/bash\ncommand -v docker >/dev/null || curl -fsSL https://get.docker.com | sh\nmkdir -p /opt/scholarpilot\n')
```

Optional daily disk snapshots: `gcloud compute resource-policies create snapshot-schedule scholarpilot-daily --region=$REGION --max-retention-days=7 --daily-schedule --start-time=02:00` and attach it with `gcloud compute disks add-resource-policies scholarpilot --zone=$ZONE --resource-policies=scholarpilot-daily`.

## 3 · Configuration

Point a DNS name at `$IP` (for sslip.io: `${IP//./-}.sslip.io` works with no setup). Then create the server's `.env`:

```bash
DOMAIN=${IP//./-}.sslip.io
cat > prod.env <<EOF
GOOGLE_CLOUD_PROJECT=$PROJECT
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
APP_PASSWORD=$(openssl rand -base64 18 | tr '+/' '-_')
SESSION_SECRET=$(openssl rand -hex 32)
EXTENSION_TOKEN=$(openssl rand -base64 24 | tr '+/' '-_')
PUBLIC_URL=https://$DOMAIN
ALLOWED_HOSTS=$DOMAIN
DOMAIN=$DOMAIN
WATCH_INTERVAL_MIN=20
EOF
cat prod.env    # note the password and the extension token
```

## 4 · Ship the code

```bash
tar --exclude=.venv --exclude=data --exclude=__pycache__ --exclude=extension --exclude=docs --exclude=tests \
    -czf /tmp/src.tgz Dockerfile docker-compose.yml docker-compose.prod.yml Caddyfile .dockerignore backend frontend
gcloud compute scp /tmp/src.tgz prod.env scholarpilot:/tmp/ --zone=$ZONE
gcloud compute ssh scholarpilot --zone=$ZONE --command='
  set -e; sudo mkdir -p /opt/scholarpilot/data && cd /opt/scholarpilot
  sudo tar xzf /tmp/src.tgz && sudo install -m 600 /tmp/prod.env .env && rm /tmp/src.tgz /tmp/prod.env
  sudo chown -R 1000:1000 data
  sudo docker compose -f docker-compose.prod.yml up -d --build'
```

Open `https://$DOMAIN`, sign in with `APP_PASSWORD`. Caddy obtains the HTTPS certificate on first request. Re-run the same commands to deploy an update; `.env` and `data/` are untouched.

## 5 · Connect Gmail (once)

1. Console → *Google Auth Platform*: set up the consent screen. Choose **Internal** if available; otherwise **External**, add yourself as a test user and **publish the app** (in "testing" mode Google expires tokens after 7 days).
2. Data access → add `gmail.readonly` and `gmail.compose`.
3. Clients → create a **Web application** client with redirect URI `https://$DOMAIN/api/gmail/callback`; download the JSON.
4. In the app: *Inbox & Gmail* → upload the JSON → *Connect Gmail*.

## 6 · Chrome extension

Load `extension/` as an unpacked extension, open its popup → *Server settings*, enter `https://$DOMAIN` and the `EXTENSION_TOKEN`.

## Operations cheat sheet

```bash
gcloud compute ssh scholarpilot --zone=$ZONE
sudo docker compose -f /opt/scholarpilot/docker-compose.prod.yml logs -f app     # logs
sudo docker compose -f /opt/scholarpilot/docker-compose.prod.yml restart         # restart
gcloud compute instances stop scholarpilot --zone=$ZONE                          # stop billing for compute
```

To change the password: edit `APP_PASSWORD` (and `SESSION_SECRET`, which signs out existing sessions) in `/opt/scholarpilot/.env`, then `sudo docker compose -f docker-compose.prod.yml up -d --force-recreate app`.

To switch domains: update `DOMAIN`, `PUBLIC_URL`, `ALLOWED_HOSTS`, recreate the stack, add the new redirect URI to the OAuth client and reconnect Gmail.
