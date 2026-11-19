#!/usr/bin/env bash
# Weekly Docker housekeeping for the whole host (every project shares one Docker). Rebuilding the crawler image a few
# times a week left ~130 GB of build cache and images nothing used (2026-11-17), filling the system disk to 85 %.
#
# Removes only what can be rebuilt or pulled again:
#   * build cache above KEEP_BUILD_CACHE (the most recently used layers stay, so the next build is still quick),
#   * dangling images (untagged leftovers of earlier builds).
# Never touches containers, volumes, networks or tagged images.
#
# Run as root: `make cleanup-timer` installs a weekly systemd timer and a guard that runs every 15 minutes with
# `--if-above 80`: it does nothing until the disk holding the images is more than 80 % full, then cleans at once and
# keeps less build cache (a day of rebuilds once took 33 GB, 2026-11-19). By hand: sudo ./deployment/scripts/docker-cleanup.sh
# The result goes to node-exporter's textfile directory, for the Grafana "Server" dashboard and DockerCleanupStale.
set -euo pipefail
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
LIMIT=""
if [ "${1:-}" = "--if-above" ]; then
  LIMIT="${2:-}"
  [[ "$LIMIT" =~ ^[0-9]+$ ]] && [ "$LIMIT" -ge 1 ] && [ "$LIMIT" -le 99 ] || { echo "--if-above takes 1-99" >&2; exit 2; }
fi
# the weekly run keeps the most recently used 20 GB of build cache; the guard, when the disk is filling, 5 GB
KEEP_BUILD_CACHE="${KEEP_BUILD_CACHE:-$([ -n "$LIMIT" ] && echo 5GB || echo 20GB)}"

# Images and build cache live under /var/lib/containerd with the containerd image store, else under Docker's root.
STORE="${STORE:-/var/lib/containerd}"
[ -d "$STORE" ] || STORE="$(docker info --format '{{.DockerRootDir}}')"
used() { df --output=used -B1 "$STORE" | tail -n 1 | tr -d ' '; }
percent() { df --output=pcent "$STORE" | tail -n 1 | tr -dc '0-9'; }

if [ -n "$LIMIT" ]; then
  now="$(percent)"
  if [ "$now" -le "$LIMIT" ]; then exit 0; fi  # the usual case: quiet, nothing touched
  echo "disk ${now} % full, above ${LIMIT} %: cleaning now"
fi
# one run at a time (the weekly timer and the guard may meet)
exec 9> "${LOCK:-/run/docker-cleanup.lock}"
flock -n 9 || { echo "another cleanup is running"; exit 0; }

before="$(used)"
echo "-> build cache above ${KEEP_BUILD_CACHE}"
docker builder prune -f --keep-storage "$KEEP_BUILD_CACHE" | tail -n 1
echo "-> dangling images"
docker image prune -f | tail -n 1
after="$(used)"
reclaimed=$(( before > after ? before - after : 0 ))
echo "reclaimed about $(( reclaimed / 1024 / 1024 )) MiB"

if [ -d "$TEXTFILE_DIR" ]; then
  tmp="$(mktemp "$TEXTFILE_DIR/.docker_cleanup.XXXXXX")"
  cat > "$tmp" <<EOF
# HELP category_docker_cleanup_last_success_timestamp_seconds When docker-cleanup.sh last finished without error.
# TYPE category_docker_cleanup_last_success_timestamp_seconds gauge
category_docker_cleanup_last_success_timestamp_seconds $(date +%s)
# HELP category_docker_cleanup_reclaimed_bytes Disk space the last run freed (used space before minus after).
# TYPE category_docker_cleanup_reclaimed_bytes gauge
category_docker_cleanup_reclaimed_bytes $reclaimed
# HELP category_docker_cleanup_last_run_by_guard 1 when the last run was the disk guard (--if-above), 0 for the weekly run.
# TYPE category_docker_cleanup_last_run_by_guard gauge
category_docker_cleanup_last_run_by_guard $([ -n "$LIMIT" ] && echo 1 || echo 0)
EOF
  chmod 644 "$tmp"
  mv "$tmp" "$TEXTFILE_DIR/docker_cleanup.prom"  # renamed into place: the exporter never reads half a file
fi
