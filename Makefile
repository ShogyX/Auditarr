.DEFAULT_GOAL := help
.PHONY: help bootstrap install dev backend frontend lint typecheck test \
        format docker docker-up docker-down docker-logs migrate rev clean

PY  := python3
UV  := uv
NPM := npm
COMPOSE := docker compose

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Install backend + frontend dev dependencies
	cd backend  && $(UV) sync --extra dev
	cd frontend && $(NPM) install

install: bootstrap ## Alias for bootstrap

dev: ## Run backend (reload) and frontend (vite) concurrently
	@( cd backend  && $(UV) run auditarr serve --reload --port 8000 ) & \
	  ( cd frontend && $(NPM) run dev ) ; wait

backend: ## Run only the backend
	cd backend && $(UV) run auditarr serve --reload --port 8000

frontend: ## Run only the frontend
	cd frontend && $(NPM) run dev

lint: ## Lint backend + frontend
	cd backend  && $(UV) run ruff check .
	cd frontend && $(NPM) run lint

typecheck: ## Type-check backend + frontend
	cd backend  && $(UV) run mypy app
	cd frontend && $(NPM) run typecheck

test: ## Run backend + frontend test suites
	cd backend  && $(UV) run pytest -q
	cd frontend && $(NPM) run test

format: ## Apply ruff + prettier
	cd backend  && $(UV) run ruff format .
	cd frontend && $(NPM) run format

docker: ## Build the docker image
	$(COMPOSE) build

docker-up: ## Start the docker-compose stack
	$(COMPOSE) up -d

docker-down: ## Stop and remove the docker-compose stack
	$(COMPOSE) down

docker-logs: ## Tail compose logs
	$(COMPOSE) logs -f --tail=200

migrate: ## Run alembic migrations
	cd backend && $(UV) run alembic upgrade head

rev: ## Create a new alembic migration: `make rev MSG="add users"`
	cd backend && $(UV) run alembic revision --autogenerate -m "$(MSG)"

clean: ## Remove build artifacts and caches
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache backend/htmlcov backend/.coverage
	rm -rf frontend/dist frontend/coverage frontend/.vite
	find . -name "__pycache__" -type d -exec rm -rf {} +
