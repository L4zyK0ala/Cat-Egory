COMPOSE = docker compose -f deployment/docker-compose.yml --env-file .env
PY ?= .venv/bin/python

.PHONY: env up down logs ps test test-unit lint migrate seed

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
