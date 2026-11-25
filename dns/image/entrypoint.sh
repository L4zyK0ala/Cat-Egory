#!/bin/sh
# Cat-egory DNS entrypoint: write Unbound's config from the environment, fetch the list once, keep it fresh in the
# background, and run Unbound in the foreground (the container stops when Unbound does).
#
#   CATEGORY_PROFILE   list to block: profile-security (default), profile-family, or one category (gambling ...)
#   CATEGORY_API       list source (default https://api.cat-egory.org)
#   UPDATE_INTERVAL    seconds between update checks (default 3600; unchanged lists are not downloaded again)
#   ALLOW_FROM         who may ask, space-separated CIDRs (default the private ranges)
#   FORWARD_TO         optional upstream resolvers, space-separated IPs; empty = resolve from the root servers
#   LOG_BLOCKED        yes = log every blocked query with the asking address (default no: nothing is logged)
#   /etc/cat-egory/allow.txt   optional, one domain per line: never blocked (subdomains included); read at start
set -eu

PROFILE="${CATEGORY_PROFILE:-profile-security}"
API="${CATEGORY_API:-https://api.cat-egory.org}"
INTERVAL="${UPDATE_INTERVAL:-3600}"
ALLOW_FROM="${ALLOW_FROM:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8}"
FORWARD_TO="${FORWARD_TO:-}"
LOG_BLOCKED="${LOG_BLOCKED:-no}"
LISTS=/var/lib/cat-egory-dns
LIST="$LISTS/$PROFILE.rpz"
CONF=/run/unbound/unbound.conf
ALLOW_ZONE="$LISTS/allow.rpz"

case "$PROFILE" in *[!a-z0-9-]*|'') echo "CATEGORY_PROFILE: letters, digits and - only" >&2; exit 2 ;; esac
case "$INTERVAL" in *[!0-9]*|'') echo "UPDATE_INTERVAL: seconds" >&2; exit 2 ;; esac
case "$LOG_BLOCKED" in yes|no) ;; *) echo "LOG_BLOCKED: yes or no" >&2; exit 2 ;; esac

# Local allow list -> an RPZ zone checked before the Cat-egory one.
{
  printf '$TTL 300\n$ORIGIN allow.local.\n@ SOA localhost. hostmaster.localhost. (1 3600 600 86400 300)\n@ NS localhost.\n'
  if [ -r /etc/cat-egory/allow.txt ]; then
    sed 's/#.*//; s/[[:space:]]//g' /etc/cat-egory/allow.txt | tr 'A-Z' 'a-z' | grep -E '^[a-z0-9_][a-z0-9_.-]*$' | sort -u |
      while read -r d; do printf '%s CNAME rpz-passthru.\n*.%s CNAME rpz-passthru.\n' "$d" "$d"; done
  fi
} > "$ALLOW_ZONE"

{
  printf 'server:\n  interface: 0.0.0.0\n  port: 53\n  do-ip6: no\n  username: ""\n  chroot: ""\n'
  printf '  directory: "%s"\n  pidfile: ""\n  use-syslog: no\n  logfile: ""\n  verbosity: 0\n' "$LISTS"
  printf '  hide-identity: yes\n  hide-version: yes\n  qname-minimisation: yes\n  prefetch: yes\n'
  printf '  module-config: "respip validator iterator"\n'
  printf '  trust-anchor-file: /usr/share/dnssec-root/trusted-key.key\n'
  printf '  access-control: 0.0.0.0/0 refuse\n'
  for net in $ALLOW_FROM; do printf '  access-control: %s allow\n' "$net"; done
  printf 'remote-control:\n  control-enable: yes\n  control-interface: /run/unbound/unbound.ctl\n  control-use-cert: no\n'
  if [ -n "$FORWARD_TO" ]; then
    printf 'forward-zone:\n  name: "."\n'
    for ip in $FORWARD_TO; do printf '  forward-addr: %s\n' "$ip"; done
  fi
  printf 'rpz:\n  name: allow.local.\n  zonefile: %s\n' "$ALLOW_ZONE"
  printf 'rpz:\n  name: rpz.cat-egory.\n  zonefile: %s\n  rpz-log: %s\n  rpz-log-name: cat-egory\n' "$LIST" "$LOG_BLOCKED"
} > "$CONF"

update() {
  cat-egory-dns-update --base "$API" --feed "$PROFILE.rpz" --out "$LIST" "$@"
}
update --init || true          # no list yet and no answer: start with an empty one, the loop retries
unbound-checkconf "$CONF" > /dev/null

(
  while sleep "$INTERVAL"; do
    update --reload "unbound-control -c $CONF auth_zone_reload rpz.cat-egory." || true
  done
) &

echo "cat-egory-dns: blocking $PROFILE from $API, checking every ${INTERVAL}s; allowed clients: $ALLOW_FROM"
exec unbound -d -c "$CONF"
