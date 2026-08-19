#!/bin/sh
set -eu

mkdir -p /suite/data/opengsc /suite/data/dashboard /suite/reports

# OpenGSC's Prisma schema must initialize an empty database, but must NOT run
# db push against the existing customized DB because Prisma would try to drop
# our additive GSC discovery tables/views.
if [ ! -s /suite/data/opengsc/prod.db ]; then
  echo "[suite] OpenGSC DB absent; initializing Prisma schema..."
  cd /suite/opengsc
  DATABASE_URL="file:/suite/data/opengsc/prod.db" npx prisma db push
else
  echo "[suite] Existing OpenGSC DB found; Prisma db push intentionally skipped."
fi

exec /usr/bin/supervisord -c /suite/runtime/supervisord.conf
