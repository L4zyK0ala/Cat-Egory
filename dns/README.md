# Cat-egory lists in company DNS servers

Work in progress: the pieces behind the site's coming "Company DNS" guide. Lists are downloaded and used
locally; the DNS server's queries never reach Cat-egory.

## `updater/cat-egory-dns-update`

A POSIX shell script (curl, sha256sum) that fetches one list, checks it and keeps the last good copy:

- `304 Not Modified` when nothing changed (the API's ETag is the list's SHA-256);
- refuses an answer whose body does not match that checksum (cut-off download), lines that are not the format's
  (a captive portal or error page), an entry count that differs from the `Entries:` header, a list that shrank
  below half of the installed one (`--force` overrides), and whatever `--check` rejects;
- installs atomically, runs `--reload`, and puts the previous list back if the reload fails;
- `--init` installs an empty list when nothing is installed yet and the download fails, so the server can start.

Any failure exits 1 and leaves the installed list in place: an unreachable API never empties the filter.

```sh
# BIND 9, hourly from cron
17 * * * * /usr/local/bin/cat-egory-dns-update --feed profile-security.rpz \
  --out /var/lib/cat-egory-dns/profile-security.rpz \
  --check "named-checkzone -q rpz.cat-egory" --reload "rndc reload rpz.cat-egory"
```

## `lab/`

BIND 9, Unbound and PowerDNS Recursor (Alpine packages) with the updater, the Unbound snippet the site shows
today, and a stand-in feed API that can fail on demand (`feedserver.py`). `sudo ./dns/lab/run.sh` builds it,
checks blocking, subdomains, a local allow list, reloads, broken feeds and restarts, then the live API, and
removes it again (`--keep` leaves it running on 127.0.0.1:5301-5305).

## `image/`: Cat-egory DNS

A recursive resolver for networks whose own DNS server cannot load an RPZ list (Windows Server DNS): Unbound with
DNSSEC validation, the updater in a loop, a local allow list, configured from the environment
(`CATEGORY_PROFILE`, `UPDATE_INTERVAL`, `ALLOW_FROM`, `FORWARD_TO`, `LOG_BLOCKED`; see `image/entrypoint.sh`). It
runs as a non-root user and logs no queries unless `LOG_BLOCKED=yes`. Windows Server DNS keeps answering for the
Active Directory zones and forwards everything else here.

```sh
docker build -f dns/image/Dockerfile -t cat-egory-dns dns
docker run -d --name cat-egory-dns -p 53:53/udp -p 53:53/tcp -v cat-egory-dns:/var/lib/cat-egory-dns cat-egory-dns
```
