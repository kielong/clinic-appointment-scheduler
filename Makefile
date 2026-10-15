.PHONY: create-admin help setup backend-setup frontend-setup db-up db-down migrate seed dev-backend dev-frontend dev generate-types format lint test test-frontend audit-backend audit-frontend audit quality-gates

BACKEND_PYTHON ?= $(if $(wildcard backend/.venv/bin/python),$(abspath backend/.venv/bin/python),$(if $(wildcard .venv/bin/python),$(abspath .venv/bin/python),python3))

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Setup ---

setup: ## Full project setup (run this first)
	@$(MAKE) backend-setup
	@$(MAKE) frontend-setup
	@$(MAKE) db-up
	@$(MAKE) migrate
	@$(MAKE) seed
	@$(MAKE) generate-types
	@echo "Setup complete. Run 'make dev' to start developing."

backend-setup: ## Create backend virtualenv and install Python dependencies
	@test -x backend/.venv/bin/python || python3 -m venv backend/.venv
	@backend/.venv/bin/python -m pip install --upgrade pip setuptools wheel
	@backend/.venv/bin/python -m pip install -e "./backend[dev]"

frontend-setup: ## Install frontend dependencies
	cd web && npm install

db-up: ## Start PostgreSQL via Docker Compose
	docker compose up -d postgres
	@echo "Waiting for PostgreSQL..."
	@until docker compose exec postgres pg_isready -U bapc > /dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL is ready."

db-down: ## Stop PostgreSQL
	docker compose down

# --- Database ---

migrate: ## Run Alembic migrations
	cd backend && $(BACKEND_PYTHON) -m alembic upgrade head

migration: ## Create a new migration (usage: make migration msg="add interviews")
	cd backend && $(BACKEND_PYTHON) -m alembic revision --autogenerate -m "$(msg)"

seed: ## Seed sample patients, appointments, and dev login accounts
	cd backend && $(BACKEND_PYTHON) -m app.seed

create-admin: ## Create/reset a clinic admin login (usage: make create-admin email=you@clinic.org name="Dr. X")
	cd backend && $(BACKEND_PYTHON) -m app.create_admin --email "$(email)" --name "$(name)"

# --- Development ---

dev-backend: ## Start the FastAPI backend (port 8000)
	cd backend && $(BACKEND_PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Start the Next.js frontend (port 3000)
	cd web && npm run dev

dev: ## Start both backend and frontend (requires two terminals)
	@echo "Run these in separate terminals:"
	@echo "  make dev-backend"
	@echo "  make dev-frontend"

# --- Schema Pipeline ---

generate-openapi: ## Export OpenAPI spec from backend
	cd backend && $(BACKEND_PYTHON) -c "import json; from app.main import app; from fastapi.openapi.utils import get_openapi; print(json.dumps(get_openapi(title=app.title, version=app.version, routes=app.routes), indent=2))" > ../web/generated/api/openapi.json

generate-types: generate-openapi ## Generate TypeScript types from OpenAPI spec
	cd web && npx @hey-api/openapi-ts -i generated/api/openapi.json -o generated/api -c @hey-api/client-fetch

# --- Quality Gates ---

format: ## Format backend code with ruff
	cd backend && $(BACKEND_PYTHON) -m ruff format .

lint: ## Lint backend code with ruff
	cd backend && $(BACKEND_PYTHON) -m ruff check . --fix

test: ## Run backend tests
	cd backend && $(BACKEND_PYTHON) -m pytest -v

test-frontend: ## Run frontend type check
	cd web && npx tsc --noEmit

audit-backend: ## Run Python dependency vulnerability audit
	rm -rf /tmp/bapc-pip-audit-cache
	cd backend && $(BACKEND_PYTHON) -m pip_audit --progress-spinner off --cache-dir /tmp/bapc-pip-audit-cache --timeout 30

audit-frontend: ## Run npm dependency vulnerability audit
	cd web && npm audit

audit: audit-backend audit-frontend ## Run dependency security audits
	@echo "Dependency audit checks passed."

quality-gates: format lint test ## Run all quality gates (format -> lint -> test)
	@echo "All quality gates passed."

# --- MCP Server ---

mcp-server: ## Start the MCP server (stdio transport)
	cd backend && $(BACKEND_PYTHON) -m app.mcp.server
