# GEO/AEO Website Auditor

A deliberately small, personal website-audit application.

Enter one public page and receive an evidence-grounded GEO/AEO readiness report built from 116 transparent checks: 56 GEO and 60 AEO. The application uses one canonical findings list for every score, count, category, priority, and detailed row.

## What it does

- Fetches one HTML page, its response headers, robots.txt, and one sitemap candidate.
- Detects or accepts the page type.
- Evaluates directly observable technical and content signals.
- Separates Pass, Partial, Fail, Unknown, Not Applicable, and Manual Review.
- Produces executive scores, prioritized fixes, category summaries, full checklists, and a review queue.
- Supports browser Print / Save PDF with report print styles.

## What it does not claim

This is a page-readiness audit. It does not measure rankings, traffic, conversions, universal AI visibility, or factual truth. It does not execute JavaScript or crawl the whole site yet. Manual and unknown items are excluded from scoring rather than converted into failures.

## Run

```bash
docker compose up --build -d
```

Open <http://127.0.0.1:8000>.

```bash
docker compose logs -f
docker compose down
```

## Test

```bash
docker compose run --rm auditor python -m unittest discover -s tests -v
```
