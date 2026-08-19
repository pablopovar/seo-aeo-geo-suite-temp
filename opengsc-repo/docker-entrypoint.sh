#!/bin/sh
set -e

# The audit suite adds extension tables/views to OpenGSC's SQLite database.
# Running `prisma db push` on every restart tries to remove those tables.
# Initialize only a new/empty database; preserve an existing database.
DB_FILE="${DATABASE_URL#file:}"

if [ ! -s "$DB_FILE" ]; then
  echo "[opengsc] initializing database schema at $DATABASE_URL ..."
  npx prisma db push
else
  echo "[opengsc] existing database detected; preserving extension tables and skipping prisma db push."
fi

echo "[opengsc] starting Next.js ..."
exec npm start
