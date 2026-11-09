# The crawler needs its AppArmor profile loaded first (make sandbox-profile). NOSANDBOX=1 = weaker fallback.
COMPOSE = docker compose -f deployment/docker-compose.yml $(if $(NOSANDBOX),-f deployment/docker-compose.nosandbox.yml) --env-file .env
PY ?= .venv/bin/python

.PHONY: env up down logs ps test test-unit lint migrate seed sandbox-profile selftest backup backup-timer restore-test docker-after-vpn

env:            ## create .env with random secrets
	./deployment/scripts/generate-env.sh

up:             ## build and start the stack
	$(COMPOSE) up -d --build

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

docker-after-vpn: ## start Docker after WireGuard, so ports bound to the VPN address can open (needs sudo)
	sudo install -D -m 644 deployment/systemd/docker-after-wireguard.conf /etc/systemd/system/docker.service.d/10-after-wireguard.conf
	sudo systemctl daemon-reload

restore-test:   ## restore the newest backup into a throwaway database (live stack untouched)
	sudo ./deployment/scripts/restore-test.sh
