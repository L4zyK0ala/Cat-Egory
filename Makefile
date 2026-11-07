# The crawler needs its AppArmor profile loaded first (make sandbox-profile). NOSANDBOX=1 = weaker fallback.
COMPOSE = docker compose -f deployment/docker-compose.yml $(if $(NOSANDBOX),-f deployment/docker-compose.nosandbox.yml) --env-file .env
PY ?= .venv/bin/python

.PHONY: env up down logs ps test test-unit lint migrate seed sandbox-profile selftest backup

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
