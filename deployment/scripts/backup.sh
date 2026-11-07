#!/usr/bin/env bash
# Back up PostgreSQL (logical dump) and the object store volume. Run from the repository root:
#   sudo ./deployment/scripts/backup.sh [backup-dir]       (default: ./backups, keeps the newest 14 of each)
# Restore: see deployment/scripts/restore.sh and documentation/OPERATIONS.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR="${1:-./backups}"
KEEP="${KEEP:-14}"
DC=(docker compose -f deployment/docker-compose.yml --env-file .env)
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$DIR"

echo "-> PostgreSQL dump"
"${DC[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=6' \
  > "$DIR/postgres-$STAMP.dump"
test -s "$DIR/postgres-$STAMP.dump"

echo "-> object store (screenshots, HTML, feeds)"
VOLUME="cat-egory_objectdata"  # compose project name is fixed in docker-compose.yml
docker run --rm -v "$VOLUME":/data:ro -v "$(cd "$DIR" && pwd)":/backup alpine:3.22 \
  tar -C /data -czf "/backup/objects-$STAMP.tar.gz" .

sha256sum "$DIR/postgres-$STAMP.dump" "$DIR/objects-$STAMP.tar.gz" > "$DIR/SHA256SUMS-$STAMP"

for prefix in postgres- objects- SHA256SUMS-; do
  ls -1t "$DIR"/"$prefix"* 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm --
done
echo "done: $DIR/*-$STAMP*"
