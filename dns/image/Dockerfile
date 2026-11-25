# syntax=docker/dockerfile:1.7
# Cat-egory DNS: a recursive resolver (Unbound) that keeps a Cat-egory list up to date and blocks what it names.
# For networks whose own DNS server cannot take an RPZ list (Windows Server DNS): point its forwarders here.
#   docker build -f dns/image/Dockerfile -t cat-egory-dns dns
# Settings are environment variables; see dns/image/entrypoint.sh.
FROM alpine:3.24
RUN apk add --no-cache unbound curl dnssec-root \
    && install -d -o unbound -g unbound /var/lib/cat-egory-dns /run/unbound
COPY --chmod=755 updater/cat-egory-dns-update /usr/local/bin/cat-egory-dns-update
COPY --chmod=755 image/entrypoint.sh /usr/local/bin/cat-egory-dns
USER unbound
EXPOSE 53/udp 53/tcp
VOLUME /var/lib/cat-egory-dns
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD unbound-control -c /run/unbound/unbound.conf status > /dev/null
ENTRYPOINT ["/usr/local/bin/cat-egory-dns"]
