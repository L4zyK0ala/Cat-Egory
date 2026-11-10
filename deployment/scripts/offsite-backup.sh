#!/usr/bin/env bash
# Encrypted off-site copy of the nightly backup with restic (run as root from the repository root):
#   sudo ./deployment/scripts/offsite-backup.sh init                 create the password file and the repository
#   sudo ./deployment/scripts/offsite-backup.sh backup [backup-dir]  newest dump + the object store (backup.sh runs this)
#   sudo ./deployment/scripts/offsite-backup.sh snapshots
#   sudo ./deployment/scripts/offsite-backup.sh check                read back 5 % of the data
#   sudo ./deployment/scripts/offsite-backup.sh restore <snapshot|latest> <empty-dir>
#
# Everything is encrypted on this machine before it leaves (restic: AES-256 + Poly1305); the storage provider sees
# only ciphertext. The object store is backed up as files, so each night uploads only what changed.
# Settings: /etc/cat-egory/offsite.env (see deployment/offsite.env.example), password: /etc/cat-egory/offsite.password.
# Keep a copy of the password away from this machine: without it the off-site copy cannot be read.
set -euo pipefail
cd "$(dirname "$0")/../.."
CONF="${OFFSITE_CONF:-/etc/cat-egory/offsite.env}"
PASSWORD="${OFFSITE_PASSWORD_FILE:-/etc/cat-egory/offsite.password}"
IMAGE="restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"
VOLUME="cat-egory_objectdata"   # same as backup.sh
CACHE="cat-egory-restic-cache"  # restic's local index cache: faster runs, nothing secret in it that the repo lacks
HOST="cat-egory"
DC=(docker compose -f deployment/docker-compose.yml --env-file .env)

private() {  # root-owned, readable by root only: the file holds a secret
  [ -f "$1" ] || { echo "missing: $1 (see documentation/OPERATIONS.md, Off-site backup)" >&2; exit 2; }
  [ "$(stat -c '%u %a' "$1")" = "0 600" ] || [ "$(stat -c '%u %a' "$1")" = "0 400" ] \
    || { echo "unsafe permissions on $1: sudo chown root:root $1 && sudo chmod 600 $1" >&2; exit 2; }
}

restic() {  # restic [extra docker args...] -- [restic args...]
  local docker_args=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do docker_args+=("$1"); shift; done
  shift
  docker run --rm --env-file "$CONF" -e RESTIC_PASSWORD_FILE=/run/restic-password \
    -v "$PASSWORD":/run/restic-password:ro -v "$CACHE":/root/.cache/restic --hostname "$HOST" \
    "${docker_args[@]}" "$IMAGE" "$@"
}

cmd="${1:-backup}"
case "$cmd" in
  init)
    install -d -m 700 -o root -g root "$(dirname "$PASSWORD")"
    if [ ! -f "$PASSWORD" ]; then
      (umask 077 && head -c 32 /dev/urandom | base64 > "$PASSWORD")
      echo "-> new password in $PASSWORD. Copy it into your password manager now (sudo cat $PASSWORD)."
    fi
    private "$CONF"; private "$PASSWORD"
    restic -- init
    ;;
  backup)
    private "$CONF"; private "$PASSWORD"
    DIR="${2:-./backups}"
    DUMP="$(ls -1t "$DIR"/postgres-*.dump | head -n 1)"
    STAMP="$(basename "$DUMP" .dump)"; STAMP="${STAMP#postgres-}"
    (cd "$DIR" && sha256sum --check --quiet "SHA256SUMS-$STAMP")  # never ship a damaged dump
    echo "-> off-site: $STAMP"
    # Fixed paths inside the container, so every snapshot looks the same and only changes are uploaded.
    restic -v "$(cd "$DIR" && pwd)/$(basename "$DUMP")":/backup/postgres.dump:ro -v "$VOLUME":/backup/objects:ro \
      -- backup --host "$HOST" --tag nightly --tag "dump-$STAMP" /backup
    restic -- forget --host "$HOST" --tag nightly --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
    restic -- check
    "${DC[@]}" exec -T redis sh -c \
      'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli hset category:task-heartbeat backup.offsite "$(date +%s)"' \
      > /dev/null || echo "warning: could not record the off-site heartbeat" >&2
    echo "-> off-site copy done"
    ;;
  snapshots)
    private "$CONF"; private "$PASSWORD"
    restic -- snapshots --host "$HOST"
    ;;
  check)
    private "$CONF"; private "$PASSWORD"
    restic -- check --read-data-subset 5%
    ;;
  restore)
    private "$CONF"; private "$PASSWORD"
    SNAP="${2:?usage: restore <snapshot|latest> <empty-dir>}"
    TARGET="${3:?usage: restore <snapshot|latest> <empty-dir>}"
    mkdir -p "$TARGET"
    [ -z "$(ls -A "$TARGET")" ] || { echo "not empty: $TARGET" >&2; exit 1; }
    restic -v "$(cd "$TARGET" && pwd)":/restore -- restore "$SNAP" --host "$HOST" --target /restore
    # The layout restore-test.sh and restore.sh expect. restic already verified every byte it restored.
    mv "$TARGET/backup/postgres.dump" "$TARGET/postgres-offsite.dump"
    (cd "$TARGET" && sha256sum postgres-offsite.dump > SHA256SUMS-offsite)
    echo "-> restored to $TARGET: postgres-offsite.dump, backup/objects/ (object store files)"
    echo "   test it: sudo ./deployment/scripts/restore-test.sh $TARGET"
    ;;
  *)
    echo "usage: $0 init|backup [dir]|snapshots|check|restore <snapshot> <dir>" >&2; exit 2
    ;;
esac
