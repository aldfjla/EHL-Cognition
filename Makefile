# Robot CI — developer entrypoints.
# No Docker anywhere: every target runs plain local processes.

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST  := $(VENV)/bin/pytest
RUFF    := $(VENV)/bin/ruff
UI_DIR  := apps/ui

.DEFAULT_GOAL := help
.PHONY: help setup api ui dev test fmt lint menagerie smoke clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv, install python packages editable, npm install the UI
	bash scripts/setup.sh

api: ## Run the FastAPI orchestrator on $$API_PORT (default 8000)
	$(UVICORN) app.main:app --app-dir apps/api --reload \
		--reload-dir apps/api --reload-dir packages \
		--host $${API_HOST:-0.0.0.0} --port $${API_PORT:-8000}

ui: ## Run the Next.js dashboard on :3000
	cd $(UI_DIR) && npm run dev

dev: ## Run api + ui together (Ctrl-C stops both)
	bash scripts/dev.sh

test: ## Run the python test suite
	$(PYTEST) -q

fmt: ## Format python (ruff) and the UI (next lint --fix)
	$(RUFF) format packages apps/api scripts
	$(RUFF) check --fix packages apps/api scripts

lint: ## Check formatting and lint without writing
	$(RUFF) format --check packages apps/api scripts
	$(RUFF) check packages apps/api scripts

menagerie: ## Download the MuJoCo Menagerie robot model library into vendor/
	bash scripts/fetch_menagerie.sh

smoke: ## Prove DEVIN_API_KEY works by creating one throwaway session
	$(PY) scripts/devin_smoke.py

clean: ## Remove venv, node_modules, caches and the local database
	rm -rf $(VENV) $(UI_DIR)/node_modules $(UI_DIR)/.next
	rm -rf .pytest_cache .ruff_cache robotci.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
