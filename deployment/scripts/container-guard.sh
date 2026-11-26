#!/usr/bin/env bash
# Container guard: what a container on this host may open towards the host itself and the home network.
#
#   sudo container-guard.sh audit     # log what would be blocked, block nothing (start here)
#   sudo container-guard.sh enforce   # log and refuse it
#   sudo container-guard.sh remove    # take every rule out again
#   sudo container-guard.sh           # the mode GUARD_MODE names in the settings file (default audit)
#   container-guard.sh print [mode]   # show the iptables commands without running them
#
# Why: every Docker network has an address on the host (its gateway), internal ones included. A container that is
# taken over can therefore open any port the host listens on for all addresses (nginx -> Kulvar, dev servers, SSH,
# the DNS admin page ...), and a container on an ordinary bridge can also reach the home network, the VPN peers and
# other projects' published ports. The guard leaves containers what they need and nothing else:
#   * to the host: DNS (53) and, from the hostmetrics network only, node-exporter (10.250.62.1:9101);
#   * forwarded: the internet, other containers, DNS to the router, and published ports listed in ALLOW_PUBLISHED;
#   * refused: every other connection to the host, to private ranges (home network, VPN) and to published ports.
# Replies to connections the host or the LAN opened are untouched; so are SSH, WireGuard and LAN clients, whose
# traffic never arrives from a Docker interface. Only IPv4: the Docker networks here have no IPv6.
#
# Settings (optional) in /etc/cat-egory/container-guard.env:
#   GUARD_MODE=audit              audit or enforce, for the systemd unit
#   ALLOW_PUBLISHED="8090"        published host ports containers may still use (e.g. Octryx for Cat-egory's worker)
#   LAN_DNS="192.168.19.1"        the router's DNS, one of Docker's upstream resolvers here
# Installed by `make container-guard` as a systemd unit that runs after Docker and again whenever Docker restarts.
set -euo pipefail

CONF="${CONTAINER_GUARD_CONF:-/etc/cat-egory/container-guard.env}"
GUARD_MODE="audit"
ALLOW_PUBLISHED=""
LAN_DNS="192.168.19.1"
# shellcheck source=/dev/null
if [ -r "$CONF" ]; then . "$CONF"; fi

MODE="${1:-$GUARD_MODE}"
PRINT=""
if [ "$MODE" = "print" ]; then PRINT=1; MODE="${2:-enforce}"; fi
case "$MODE" in audit|enforce|remove) ;; *) sed -n '2,9p' "$0" >&2; exit 2 ;; esac

ipt() { if [ -n "$PRINT" ]; then echo "iptables -w $*"; else iptables -w "$@"; fi; }
# true when the rule is already there (print mode: never, so every command is shown)
has() { [ -z "$PRINT" ] && iptables -w -C "$@" 2> /dev/null; }

HOOKS=("INPUT -i br-+ -j CG-IN" "INPUT -i docker0 -j CG-IN"
       "DOCKER-USER -i br-+ -j CG-FWD" "DOCKER-USER -i docker0 -j CG-FWD")

unhook() {
  for hook in "${HOOKS[@]}"; do
    # shellcheck disable=SC2086
    while has $hook; do ipt -D $hook; done
  done
}

if [ "$MODE" = "remove" ]; then
  unhook
  for chain in CG-IN CG-FWD CG-BLOCK; do
    if [ -n "$PRINT" ] || iptables -w -n -L "$chain" > /dev/null 2>&1; then ipt -F "$chain"; ipt -X "$chain"; fi
  done
  echo "container guard removed"
  exit 0
fi

if [ -z "$PRINT" ] && ! iptables -w -n -L DOCKER-USER > /dev/null 2>&1; then
  echo "DOCKER-USER chain missing: start Docker first" >&2
  exit 1
fi

for chain in CG-IN CG-FWD CG-BLOCK; do
  if [ -n "$PRINT" ] || ! iptables -w -n -L "$chain" > /dev/null 2>&1; then ipt -N "$chain"; fi
  ipt -F "$chain"
done

# What is not allowed: logged (at most 20 lines a minute), then refused, or let through in audit mode.
ipt -A CG-BLOCK -m limit --limit 20/min --limit-burst 20 -j LOG --log-prefix "container-guard($MODE): " --log-level 4
if [ "$MODE" = "enforce" ]; then
  ipt -A CG-BLOCK -p tcp -j REJECT --reject-with tcp-reset
  ipt -A CG-BLOCK -j REJECT --reject-with icmp-admin-prohibited
fi
# audit: CG-BLOCK ends without a verdict; it is entered with -g, so processing resumes after the hook

# From a container to the host itself.
ipt -A CG-IN -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
ipt -A CG-IN -m addrtype --src-type LOCAL -j RETURN   # the host's own multicast (mDNS) looped back on a bridge
ipt -A CG-IN -p udp --dport 53 -j RETURN
ipt -A CG-IN -p tcp --dport 53 -j RETURN
ipt -A CG-IN -s 10.250.62.0/29 -d 10.250.62.1 -p tcp --dport 9101 -j RETURN
ipt -A CG-IN -g CG-BLOCK

# From a container through the host (internet, other containers, home network).
ipt -A CG-FWD -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for port in $ALLOW_PUBLISHED; do
  ipt -A CG-FWD -p tcp -m conntrack --ctstate DNAT --ctorigdstport "$port" -j RETURN
done
ipt -A CG-FWD -m conntrack --ctstate DNAT -g CG-BLOCK   # another project's (or our own) published port
ipt -A CG-FWD -o br-+ -j RETURN                         # container to container: Docker's own isolation applies
ipt -A CG-FWD -o docker0 -j RETURN
for dns in $LAN_DNS; do
  ipt -A CG-FWD -d "$dns" -p udp --dport 53 -j RETURN
  ipt -A CG-FWD -d "$dns" -p tcp --dport 53 -j RETURN
done
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
  ipt -A CG-FWD -d "$net" -g CG-BLOCK
done

unhook
# shellcheck disable=SC2086
for hook in "${HOOKS[@]}"; do set -- $hook; chain="$1"; shift; ipt -I "$chain" 1 "$@"; done
echo "container guard: $MODE (allowed published ports: ${ALLOW_PUBLISHED:-none}; LAN DNS: ${LAN_DNS:-none})"
