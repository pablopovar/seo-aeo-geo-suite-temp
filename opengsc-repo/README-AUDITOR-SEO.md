# Auditor SEO Monitor

Local SEO keyword discovery / traffic review tool based on OpenGSC.

## Location

```text
/home/pablo/ownCloud/Projects/ai-labs/ai-observer/projects/aeo-geo-seo-auditor/auditor-seo-repo
```

## Start

```bash
cd "/home/pablo/ownCloud/Projects/ai-labs/ai-observer/projects/aeo-geo-seo-auditor/auditor-seo-repo"
./run-seo-monitor.sh
```

Open:

```text
http://localhost:3017
```

## Required Google OAuth settings

In Google Cloud Console, create an OAuth 2.0 Client ID.

Authorized JavaScript origin:

```text
http://localhost:3017
```

Authorized redirect URI:

```text
http://localhost:3017/api/auth/callback/google
```

Then edit:

```bash
/home/pablo/ownCloud/Projects/ai-labs/ai-observer/projects/aeo-geo-seo-auditor/auditor-seo-repo/.env
```

Set:

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
```

## Docker files

This local setup uses:

```text
compose.auditor-seo.yml
.env
run-seo-monitor.sh
stop-seo-monitor.sh
```

The app database lives in Docker volume:

```text
auditor-seo-data
```

## Upstream

The upstream remote is named:

```text
upstream
```

Update manually with:

```bash
git fetch upstream main
git merge --ff-only upstream/main
```
