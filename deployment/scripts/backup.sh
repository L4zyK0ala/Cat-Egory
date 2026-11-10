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

# Written under dot-names and renamed once checked: a failed run must leave nothing that retention counts as a backup.
DUMP_TMP="$DIR/.postgres-$STAMP.tmp"
OBJECTS_TMP="$DIR/.objects-$STAMP.tmp"
trap 'rm -f "$DUMP_TMP" "$OBJECTS_TMP"' EXIT

echo "-> PostgreSQL dump"
"${DC[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=6' \
  > "$DUMP_TMP"
test -s "$DUMP_TMP"
"${DC[@]}" exec -T postgres pg_restore --list < "$DUMP_TMP" > /dev/null  # reads the whole table of contents

echo "-> object store (screenshots, HTML, feeds)"
VOLUME="cat-egory_objectdata"  # compose project name is fixed in docker-compose.yml
docker run --rm -v "$VOLUME":/data:ro -v "$(cd "$DIR" && pwd)":/backup alpine:3.22 \
  tar -C /data -czf "/backup/.objects-$STAMP.tmp" .
gzip -t "$OBJECTS_TMP"

mv "$DUMP_TMP" "$DIR/postgres-$STAMP.dump"
mv "$OBJECTS_TMP" "$DIR/objects-$STAMP.tar.gz"

(cd "$DIR" && sha256sum "postgres-$STAMP.dump" "objects-$STAMP.tar.gz" > "SHA256SUMS-$STAMP")

prune() {  # newest $2 of each kind stay in $1
  for prefix in postgres- objects- SHA256SUMS-; do
    ls -1t "$1"/"$prefix"* 2>/dev/null | tail -n +$(($2 + 1)) | xargs -r rm --
  done
}
prune "$DIR" "$KEEP"

copy_to() {  # copy this backup to $1 (on a mounted disk), verify it there, keep the newest $2
  local dest="$1" keep="$2"
  # An unmounted disk leaves an empty directory on the system disk: never fill that by mistake.
  mountpoint -q "$(dirname "$dest")" || { echo "not mounted: $(dirname "$dest")" >&2; return 1; }
  mkdir -p "$dest"
  rsync -t "$DIR/postgres-$STAMP.dump" "$DIR/objects-$STAMP.tar.gz" "$DIR/SHA256SUMS-$STAMP" "$dest/" || return 1
  (cd "$dest" && sha256sum --check --quiet "SHA256SUMS-$STAMP") || return 1
  prune "$dest" "$keep"
  echo "-> copied to $dest"
}

# BACKUP_COPY_TO must work: a failure ends the run without the heartbeat, so BackupStale fires.
# BACKUP_COPY_OPTIONAL is a spare (a removed USB stick is only a warning) and keeps fewer sets.
for dest in ${BACKUP_COPY_TO:-}; do
  copy_to "$dest" "$KEEP" || { echo "error: copy to $dest failed" >&2; exit 1; }
done
for dest in ${BACKUP_COPY_OPTIONAL:-}; do
  copy_to "$dest" "${KEEP_OPTIONAL:-3}" || echo "warning: optional copy to $dest failed" >&2
done

# Seen by the BackupStale alert. A backup that succeeded stays a success if this one line fails.
"${DC[@]}" exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli hset category:task-heartbeat backup.nightly "$(date +%s)"' \
  > /dev/null || echo "warning: could not record the backup heartbeat" >&2

# Encrypted copy away from this machine, once set up (offsite-backup.sh init). Its own heartbeat and alert
# (OffsiteBackupStale): a provider outage must not mark the local backup as failed.
if [ -f "${OFFSITE_CONF:-/etc/cat-egory/offsite.env}" ]; then
  ./deployment/scripts/offsite-backup.sh backup "$DIR" || echo "warning: off-site copy failed" >&2
fi
echo "done: $DIR/*-$STAMP*"
