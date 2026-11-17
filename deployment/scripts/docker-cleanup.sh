#!/usr/bin/env bash
# Weekly Docker housekeeping for the whole host (every project shares one Docker). Rebuilding the crawler image a few
# times a week left ~130 GB of build cache and images nothing used (2026-11-17), filling the system disk to 85 %.
#
# Removes only what can be rebuilt or pulled again:
#   * build cache above KEEP_BUILD_CACHE (the most recently used layers stay, so the next build is still quick),
#   * dangling images (untagged leftovers of earlier builds).
# Never touches containers, volumes, networks or tagged images.
#
# Run as root: `make cleanup-timer` installs a weekly systemd timer. By hand: sudo ./deployment/scripts/docker-cleanup.sh
# The result goes to node-exporter's textfile directory, for the Grafana "Server" dashboard and DockerCleanupStale.
set -euo pipefail
KEEP_BUILD_CACHE="${KEEP_BUILD_CACHE:-20GB}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"

# Images and build cache live under /var/lib/containerd with the containerd image store, else under Docker's root.
STORE=/var/lib/containerd
[ -d "$STORE" ] || STORE="$(docker info --format '{{.DockerRootDir}}')"
used() { df --output=used -B1 "$STORE" | tail -n 1 | tr -d ' '; }

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
EOF
  chmod 644 "$tmp"
  mv "$tmp" "$TEXTFILE_DIR/docker_cleanup.prom"  # renamed into place: the exporter never reads half a file
fi
