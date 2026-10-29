.DEFAULT_GOAL := help
.PHONY: help install up down logs fmt lint test demo clean

export ESIGN_API_KEY ?= dev-api-key-change-me

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install dependencies into .venv
	uv sync --locked

up: ## Build and start api + mongo, wait until healthy
	docker compose up --build --detach --wait

down: ## Stop the stack (data volumes are kept)
	docker compose down

logs: ## Follow the API logs
	docker compose logs --follow api

fmt: ## Format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .

lint: ## Lint, format check and strict type check
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test: ## Run all tests with coverage (needs Docker for MongoDB)
	uv run pytest --cov --cov-report=term-missing

demo: ## Run the full flow against the running stack; writes out/signed.pdf
	uv run python scripts/demo.py
	@echo "==> Independent check with the pyHanko CLI"
	uv run pyhanko sign validate --pretty-print --trust out/test-ca.pem --trust-replace out/signed.pdf

clean: ## Remove caches and generated output
	rm -rf out .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
