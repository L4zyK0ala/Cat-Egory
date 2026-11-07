#!/usr/bin/env sh
# Create .env from .env.example with random infrastructure secrets. Refuses to overwrite an existing .env.
set -eu
cd "$(dirname "$0")/../.."
if [ -e .env ]; then
  echo ".env already exists; not overwriting." >&2
  exit 1
fi
rand() { head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-32}"; }
umask 077
sed -e "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$(rand 32)/" \
    -e "s/^REDIS_PASSWORD=$/REDIS_PASSWORD=$(rand 32)/" \
    -e "s/^S3_ACCESS_KEY=$/S3_ACCESS_KEY=$(rand 20)/" \
    -e "s/^S3_SECRET_KEY=$/S3_SECRET_KEY=$(rand 40)/" \
    -e "s/^GRAFANA_ADMIN_PASSWORD=$/GRAFANA_ADMIN_PASSWORD=$(rand 24)/" \
    -e "s/^CATEGORY_FINGERPRINT_SECRET=$/CATEGORY_FINGERPRINT_SECRET=$(rand 40)/" \
    .env.example > .env
echo "Created .env (mode 600). Add CATEGORY_EVREN_API_KEY yourself if you use EVREN."
