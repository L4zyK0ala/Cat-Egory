#!/usr/bin/env bash
# DNS lab: does a Cat-egory list block what it should, leave the rest alone, honour a local allow list, pick up a
# new version, and survive a broken or unreachable feed? For BIND 9, Unbound and PowerDNS Recursor with
# cat-egory-dns-update, plus the Unbound snippet the site shows today (url:), and the live API.
#   sudo ./dns/lab/run.sh            # build, test, print the results, remove the lab
#   sudo ./dns/lab/run.sh --keep     # leave the lab running afterwards (ports 5301-5305, 5380 on 127.0.0.1)
# Needs Docker and dig on the host. Takes about five minutes (failed downloads are retried before giving up).
set -uo pipefail
cd "$(dirname "$0")"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
DC=(docker compose -p cat-egory-dnslab -f compose.yml)
declare -A PORT=([bind]=5301 [unbound]=5302 [pdns]=5303 [unbound-url]=5304 [unbound-url-file]=5305 [catdns]=5306)
PASS=0
FAIL=0
FAILED=()

teardown() { [ "$KEEP" = 1 ] || "${DC[@]}" down -v --remove-orphans > /dev/null 2>&1; }
trap teardown EXIT

status() { dig +time=3 +tries=2 @127.0.0.1 -p "${PORT[$1]}" "$2" A 2> /dev/null | sed -n 's/.*status: \([A-Z]*\),.*/\1/p'; }
mode() { curl -fsS "http://127.0.0.1:5380/_mode/$1" > /dev/null; }
# status_after <server> <name> <wanted>: a reload may finish after rndc/unbound-control return (BIND rebuilds its
# policy in the background), so ask for up to 5 seconds until the answer is the wanted one
status_after() {
  local got=""
  for _ in $(seq 1 "${4:-10}"); do got="$(status "$1" "$2")"; [ "$got" = "$3" ] && break; sleep 0.5; done
  echo "$got"
}
expect() {  # expect <label> <wanted> <got>
  if [ "$2" = "$3" ]; then PASS=$((PASS + 1)); printf '  ok    %-58s %s\n' "$1" "$3"
  else FAIL=$((FAIL + 1)); FAILED+=("$1"); printf '  FAIL  %-58s %s (wanted %s)\n' "$1" "${3:-no answer}" "$2"; fi
}
info() { printf '  info  %-58s %s\n' "$1" "$2"; }

reload_cmd() {
  case "$1" in
    bind) echo "rndc -k /etc/bind/rndc.key reload rpz.cat-egory" ;;
    unbound) echo "unbound-control -c /etc/unbound/unbound.conf auth_zone_reload rpz.cat-egory." ;;
    pdns) echo "rec_control --socket-dir=/run/pdns-recursor reload-lua-config && rec_control --socket-dir=/run/pdns-recursor wipe-cache '.\$'" ;;
  esac
}
check_cmd() { [ "$1" = bind ] && echo "named-checkzone -q rpz.cat-egory" || echo ""; }
# update <server> [extra args]: run the updater inside the server's container; prints its log line, returns its exit
update() {
  local srv="$1"; shift
  "${DC[@]}" exec -T "$srv" cat-egory-dns-update --base http://feeds:8000 --feed profile-security.rpz \
    --out /var/lib/cat-egory-dns/profile-security.rpz --check "$(check_cmd "$srv")" --reload "$(reload_cmd "$srv")" \
    "$@" < /dev/null 2>&1 | tail -n 1 | sed 's/^/        /'
  return "${PIPESTATUS[0]}"
}
forget_etag() { "${DC[@]}" exec -T "$1" rm -f /var/lib/cat-egory-dns/profile-security.rpz.etag < /dev/null; }
wait_ready() {
  for _ in $(seq 1 60); do [ "$(status "$1" example.org)" = NOERROR ] && return 0; sleep 2; done
  return 1
}

echo "-> building and starting the lab"
"${DC[@]}" up -d --build --quiet-pull > /dev/null 2>&1 || { "${DC[@]}" up -d --build; exit 1; }
for srv in bind unbound pdns unbound-url unbound-url-file catdns; do
  wait_ready "$srv" || { echo "$srv did not answer; its log:"; "${DC[@]}" logs --tail 30 "$srv"; FAIL=$((FAIL + 1)); FAILED+=("$srv start"); }
done

for srv in bind unbound pdns; do
  echo "-> $srv"
  expect "$srv: listed name is blocked (example.com)" NXDOMAIN "$(status "$srv" example.com)"
  expect "$srv: its subdomains too (www.example.com)" NXDOMAIN "$(status "$srv" www.example.com)"
  expect "$srv: other names resolve (example.org)" NOERROR "$(status "$srv" example.org)"
  expect "$srv: local allow list wins (example.net)" NOERROR "$(status "$srv" example.net)"

  update "$srv"; expect "$srv: same version again = no download, exit 0" 0 "$?"

  mode plus
  update "$srv"; expect "$srv: new version installed and reloaded, exit 0" 0 "$?"
  expect "$srv: new name blocked after reload (www.example.org, not cached)" NXDOMAIN "$(status_after "$srv" www.example.org NXDOMAIN)"
  expect "$srv: and the cached one (example.org)" NXDOMAIN "$(status_after "$srv" example.org NXDOMAIN)"
  mode ok
  update "$srv"; expect "$srv: back to the first version, exit 0" 0 "$?"
  expect "$srv: example.org resolves again" NOERROR "$(status_after "$srv" example.org NOERROR)"

  for m in error garbage truncated shrunk; do
    mode "$m"; forget_etag "$srv"
    update "$srv"; expect "$srv: feed '$m' is refused, exit 1" 1 "$?"
    expect "$srv: after '$m' the list still blocks example.com" NXDOMAIN "$(status "$srv" example.com)"
    expect "$srv: after '$m' example.org still resolves" NOERROR "$(status "$srv" example.org)"
  done

  mode error
  "${DC[@]}" restart "$srv" > /dev/null 2>&1
  wait_ready "$srv"
  expect "$srv: restarted while the feed is down: still blocks" NXDOMAIN "$(status "$srv" example.com)"
  mode ok
done

for srv in unbound-url unbound-url-file; do
  echo "-> $srv (Unbound downloads the zone itself; unbound-url is the site's snippet, -file adds a zonefile)"
  expect "$srv: listed name is blocked (example.com)" NXDOMAIN "$(status "$srv" example.com)"
  for m in error garbage; do
    mode "$m"
    "${DC[@]}" restart "$srv" > /dev/null 2>&1
    wait_ready "$srv"
    expect "$srv: restarted with feed '$m': DNS still answers" NOERROR "$(status "$srv" example.org)"
    info "$srv: restarted with feed '$m': example.com is" "$(status "$srv" example.com)"
  done
  mode ok
done

echo "-> catdns (the Cat-egory DNS image: Unbound + updater loop every 3 s in the lab)"
srv=catdns
expect "$srv: listed name is blocked (example.com)" NXDOMAIN "$(status "$srv" example.com)"
expect "$srv: its subdomains too (www.example.com)" NXDOMAIN "$(status "$srv" www.example.com)"
expect "$srv: other names resolve (example.org)" NOERROR "$(status "$srv" example.org)"
expect "$srv: allow.txt wins (example.net)" NOERROR "$(status "$srv" example.net)"
mode plus
expect "$srv: picks up a new version by itself (www.example.org)" NXDOMAIN "$(status_after "$srv" www.example.org NXDOMAIN 40)"
mode ok
expect "$srv: and the next one (example.org resolves again)" NOERROR "$(status_after "$srv" example.org NOERROR 40)"
for m in error garbage truncated shrunk; do
  mode "$m"; sleep 8
  expect "$srv: feed '$m' for 8 s: still blocks example.com" NXDOMAIN "$(status "$srv" example.com)"
done
mode error
"${DC[@]}" restart "$srv" > /dev/null 2>&1
wait_ready "$srv"
expect "$srv: restarted while the feed is down: still blocks" NXDOMAIN "$(status "$srv" example.com)"
mode ok
expect "$srv: logs no queries by default (LOG_BLOCKED=no)" 0 "$("${DC[@]}" logs "$srv" 2>&1 | grep -c 'rpz: applied')"
expect "$srv: runs as a non-root user" unbound "$("${DC[@]}" exec -T "$srv" whoami < /dev/null 2>/dev/null)"

echo "-> live API (https://api.cat-egory.org), checked by BIND's zone checker"
for feed in profile-security.rpz profile-family.rpz; do
  "${DC[@]}" exec -T bind cat-egory-dns-update --feed "$feed" --out "/tmp/live/$feed" \
    --check "named-checkzone -q rpz.cat-egory" < /dev/null 2>&1 | tail -n 1 | sed 's/^/        /'
  expect "live: $feed downloads, verifies and passes named-checkzone" 0 "${PIPESTATUS[0]}"
done

echo
echo "$PASS passed, $FAIL failed"
for f in "${FAILED[@]}"; do echo "  failed: $f"; done
if [ "$FAIL" -gt 0 ]; then
  echo "server logs (last lines):"
  for srv in bind unbound pdns unbound-url unbound-url-file catdns; do echo "--- $srv"; "${DC[@]}" logs --tail 8 "$srv" 2>&1 | sed 's/^/  /'; done
fi
[ "$FAIL" = 0 ]
