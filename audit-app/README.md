# Audit App

This directory contains the project's own SEO / GEO / AEO audit application.

OpenGSC is kept separately under `../opengsc-repo/`.

## Runtime

The root `docker-compose.yml` is authoritative.

- OpenGSC: port 4017
- Audit App: port 4018

## Structure

- `app.py` — current Flask application shell
- `audits/geo_aeo/` — GEO/AEO capture and scoring
- `crawlers/` — crawl/discovery functionality
- `integrations/dataforseo/` — DataForSEO integration
- `services/` — application services
- `templates/` and `static/` — UI

## OpenGSC integration

For now the Audit App keeps the existing direct read-only SQLite integration.

The `opengsc-data` Docker volume is mounted writable into OpenGSC and read-only into the Audit App.
