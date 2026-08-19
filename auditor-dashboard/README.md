# Auditor Dashboard

Thin domain-first dashboard sitting beside:

- `auditor-seo-repo` — OpenGSC-derived SEO/GSC/GA4 engine
- `auditor-aeo-geo-repo` — GEO/AEO audit engine

Default host port: **4018**.

## Current MVP

1. Select domain
2. Domain overview
   - observed GSC keywords
   - ranking pages
   - impressions / clicks / CTR
   - observed position range
   - current/stale keyword activity
3. Browse pages
4. Page detail
   - all observed ranking keywords
   - best / average / latest / worst observed position
   - impressions / clicks / recency status
5. Reports
   - browse mounted GEO/AEO report files
   - open/export files
   - Run Audit bridge

## Installation location

Place this directory at:

```text
/home/pablo/ownCloud/Projects/ai-labs/ai-observer/projects/aeo-geo-seo-auditor/auditor-dashboard
```

so the project tree becomes:

```text
aeo-geo-seo-auditor/
├── auditor-aeo-geo-repo/
├── auditor-seo-repo/
├── auditor-dashboard/
└── reports/
```

## Start

```bash
cd auditor-dashboard
cp .env.example .env
docker compose up -d --build
```

Open:

```text
http://localhost:4018
```

## Required existing Docker volume

The compose file reads OpenGSC's existing named volume:

```text
auditor-seo-repo_opengsc-data
```

It is mounted read-only.

Confirm with:

```bash
docker volume ls | grep opengsc
```

## GEO/AEO integration

The dashboard intentionally does not merge the two engines.

The first version:

- lists/exports files from `../reports`
- exposes a Run GEO/AEO Audit action
- defaults that action to opening the existing auditor on port 8000

To make the Run button execute an audit directly, set `AEO_AUDIT_COMMAND`
once the exact existing auditor invocation is known.

The page-level GEO/AEO panel is an adapter point. Once the current
`auditor-aeo-geo-repo` output/API schema is mapped, its findings can be
joined by canonical URL without changing the SEO engine.
