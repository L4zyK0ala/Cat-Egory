#!/usr/bin/env bash
# Prove that the newest backup can be restored, without touching the running stack: check the checksums, then
# restore the dump into a throwaway PostgreSQL container that has no network and is removed afterwards.
#   sudo ./deployment/scripts/restore-test.sh [backup-dir]
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR="${1:-./backups}"
IMAGE="postgres:18.6-alpine"  # same as docker-compose.yml
NAME="cat-egory-restore-test"
DUMP="$(ls -1t "$DIR"/postgres-*.dump | head -n 1)"
STAMP="$(basename "$DUMP" .dump)"; STAMP="${STAMP#postgres-}"

echo "-> checksums of $STAMP"
sha256sum --check --quiet "$DIR/SHA256SUMS-$STAMP"

echo "-> restoring into a throwaway database"
trap 'docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT
docker run -d --rm --name "$NAME" --network none -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_USER=category -e POSTGRES_DB=category -v "$(cd "$DIR" && pwd)/$(basename "$DUMP")":/dump:ro "$IMAGE" > /dev/null
for _ in $(seq 1 60); do
  docker exec "$NAME" pg_isready -q -h 127.0.0.1 -U category && break
  sleep 1
done
docker exec "$NAME" pg_isready -q -h 127.0.0.1 -U category
docker exec "$NAME" pg_restore -U category -d category --no-owner --exit-on-error /dump

echo "-> what came back"
docker exec "$NAME" psql -U category -d category -At -F ': ' -c "
  select 'domains', count(*) from domains
  union all select 'verified domains', count(*) from domains where classification_status = 'verified'
  union all select 'threat records', count(*) from threat_intelligence
  union all select 'review tasks', count(*) from review_tasks"
echo "ok: $STAMP restores"
