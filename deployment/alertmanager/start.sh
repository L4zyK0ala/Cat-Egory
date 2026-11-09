#!/bin/sh
# Builds the Alertmanager configuration from the environment. The token goes to a tmpfs file and never into the
# configuration text; the environment is cleared before Alertmanager starts.
set -eu
umask 077
CONF=/etc/alertmanager
if [ -n "${ALERT_TELEGRAM_TOKEN:-}" ] && [ -n "${ALERT_TELEGRAM_CHAT_ID:-}" ]; then
  case "$ALERT_TELEGRAM_CHAT_ID" in
    *[!0-9-]*) echo "CATEGORY_ALERT_TELEGRAM_CHAT_ID must be a number" >&2; exit 1 ;;
  esac
  printf '%s' "$ALERT_TELEGRAM_TOKEN" > /tmp/telegram-token
  sed "s/__CHAT_ID__/$ALERT_TELEGRAM_CHAT_ID/" "$CONF/telegram.yml" > /tmp/alertmanager.yml
  echo "alerts: critical ones go to Telegram" >&2
else
  cp "$CONF/silent.yml" /tmp/alertmanager.yml
  echo "alerts: no Telegram settings, nothing is sent" >&2
fi
unset ALERT_TELEGRAM_TOKEN ALERT_TELEGRAM_CHAT_ID
exec /bin/alertmanager --config.file=/tmp/alertmanager.yml --storage.path=/tmp/data --cluster.listen-address=
