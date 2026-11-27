# The crawler needs its AppArmor profile loaded first (make sandbox-profile). NOSANDBOX=1 = weaker fallback.
# The `public` profile (cloudflared) is on once .env has a tunnel token, so every `make up` keeps the tunnel running.
# With it comes docker-compose.public.yml: the public site and API leave the `edge` network (no route to the home
# network or the internet; :3010 and :8110 stop answering on the LAN). The host itself: `make container-guard`.
PUBLIC ?= $(shell grep -qsE '^CATEGORY_TUNNEL_TOKEN=.+' .env && echo 1)
COMPOSE = docker compose -f deployment/docker-compose.yml $(if $(NOSANDBOX),-f deployment/docker-compose.nosandbox.yml) \
	$(if $(PUBLIC),-f deployment/docker-compose.public.yml) --env-file .env $(if $(PUBLIC),--profile public)
PY ?= .venv/bin/python

.PHONY: env up rebuild down logs ps test test-unit lint migrate seed sandbox-profile selftest backup backup-timer restore-test docker-after-vpn offsite-init offsite-check cve-timer cve-check container-guard

env:            ## create .env with random secrets
	./deployment/scripts/generate-env.sh

up:             ## build and start the stack
	$(COMPOSE) up -d --build

# `make up` keeps cached layers, so `apk upgrade` / `apt-get upgrade` steps do not run again and package fixes from
# the CVE watch never arrive. This pulls the base images and builds without the cache. SERVICES=postgres limits it.
rebuild:        ## pull base images, build without the cache, restart (SERVICES=... for some)
	$(COMPOSE) build --no-cache --pull $(SERVICES)
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test:           ## unit + integration (integration needs CATEGORY_TEST_DATABASE_URL)
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest tests/unit

lint:
	$(PY) -m ruff check .

migrate:
	$(PY) -m alembic upgrade head

seed:
	$(PY) -m database.seed

sandbox-profile: ## install + load the crawler AppArmor profile (needs sudo; survives reboots)
	sudo cp deployment/apparmor/cat-egory-crawler /etc/apparmor.d/
	sudo apparmor_parser -r -W /etc/apparmor.d/cat-egory-crawler

selftest:       ## crawler isolation / sandbox self-test
	$(COMPOSE) exec crawler python -m crawler.selftest

backup:
	sudo ./deployment/scripts/backup.sh

backup-timer:   ## install the nightly backup timer (needs sudo)
	sed 's#@ROOT@#$(CURDIR)#' deployment/systemd/cat-egory-backup.service | sudo tee /etc/systemd/system/cat-egory-backup.service > /dev/null
	sudo cp deployment/systemd/cat-egory-backup.timer /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable --now cat-egory-backup.timer

cleanup-timer:  ## install the weekly Docker housekeeping, its 80 % disk guard and node-exporter's textfile directory (needs sudo)
	sudo install -d -m 755 /var/lib/node_exporter/textfile
	sed 's#@ROOT@#$(CURDIR)#' deployment/systemd/docker-cleanup.service | sudo tee /etc/systemd/system/docker-cleanup.service > /dev/null
	sed 's#@ROOT@#$(CURDIR)#' deployment/systemd/docker-cleanup-guard.service | sudo tee /etc/systemd/system/docker-cleanup-guard.service > /dev/null
	sudo cp deployment/systemd/docker-cleanup.timer deployment/systemd/docker-cleanup-guard.timer /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable --now docker-cleanup.timer docker-cleanup-guard.timer

docker-after-vpn: ## start Docker after WireGuard, so ports bound to the VPN address can open (needs sudo)
	sudo install -D -m 644 deployment/systemd/docker-after-wireguard.conf /etc/systemd/system/docker.service.d/10-after-wireguard.conf
	sudo systemctl daemon-reload

cve-timer:      ## install the daily CVE watch (Trivy over the stack's images, new findings to Telegram; needs sudo)
	sed 's#@ROOT@#$(CURDIR)#' deployment/systemd/cat-egory-cve-watch.service | sudo tee /etc/systemd/system/cat-egory-cve-watch.service > /dev/null
	sudo cp deployment/systemd/cat-egory-cve-watch.timer /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable --now cat-egory-cve-watch.timer

cve-check:      ## scan now and print what would be sent (sends and remembers nothing; needs sudo)
	sudo python3 deployment/scripts/cve_watch.py --dry-run

container-guard: ## install the container guard (iptables: containers vs host and home network; audit mode first; needs sudo)
	sudo install -m 755 deployment/scripts/container-guard.sh /usr/local/sbin/container-guard.sh
	sudo install -m 644 deployment/systemd/container-guard.service /etc/systemd/system/container-guard.service
	sudo test -e /etc/cat-egory/container-guard.env || sudo install -D -m 644 deployment/systemd/container-guard.env /etc/cat-egory/container-guard.env
	sudo systemctl daemon-reload
	sudo systemctl enable container-guard.service
	sudo systemctl restart container-guard.service

offsite-init:   ## create the encrypted off-site repository (needs /etc/cat-egory/offsite.env)
	sudo ./deployment/scripts/offsite-backup.sh init

offsite-check:  ## list off-site snapshots and read back 5 % of the data
	sudo ./deployment/scripts/offsite-backup.sh snapshots
	sudo ./deployment/scripts/offsite-backup.sh check

restore-test:   ## restore the newest backup into a throwaway database (live stack untouched)
	sudo ./deployment/scripts/restore-test.sh
