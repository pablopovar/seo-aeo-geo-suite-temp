#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f compose.auditor-seo.yml up -d --build
docker compose -f compose.auditor-seo.yml ps
