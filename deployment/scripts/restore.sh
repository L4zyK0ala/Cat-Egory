#!/usr/bin/env bash
# Restore a PostgreSQL dump made by backup.sh into the running stack. DESTRUCTIVE: replaces the database.
#   sudo ./deployment/scripts/restore.sh backups/postgres-<stamp>.dump
set -euo pipefail
cd "$(dirname "$0")/../.."
DUMP="${1:?usage: restore.sh <postgres-*.dump>}"
DC=(docker compose -f deployment/docker-compose.yml --env-file .env)
read -r -p "This replaces the current database with $DUMP. Type 'restore' to continue: " answer
[ "$answer" = "restore" ] || { echo "aborted"; exit 1; }
"${DC[@]}" stop api admin-api worker worker-scan worker-fast beat crawler ct-watch frontend admin-ui
"${DC[@]}" exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < "$DUMP"
"${DC[@]}" up -d
echo "restored; check: ${DC[*]} ps"
