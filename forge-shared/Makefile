.PHONY: help install dev-install test lint format type-check clean build upload docs

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install package
	uv pip install -e .

dev-install:  ## Install package with development dependencies
	uv pip install -e ".[dev]"

test:  ## Run tests
	pytest

test-cov:  ## Run tests with coverage
	pytest --cov=forge_shared --cov-report=html --cov-report=term

test-verbose:  ## Run tests with verbose output
	pytest -v -s

lint:  ## Run linting
	ruff check forge_shared tests

format:  ## Format code with ruff
	ruff format forge_shared tests

format-check:  ## Check code formatting
	ruff format --check forge_shared tests

type-check:  ## Run type checking with mypy
	mypy forge_shared

clean:  ## Clean up build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .ruff_cache/
	rm -rf .mypy_cache/
	rm -rf htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

build:  ## Build package
	hatchling build

upload:  ## Upload to PyPI
	hatchling publish

docs:  ## Generate documentation
	@echo "Documentation available at README.md"

check-all: lint type-check test  ## Run all checks (lint, type-check, test)

ci: format-check lint type-check test-cov  ## Run CI pipeline locally

run-example:  ## Run example FastAPI app
	uicorn examples.fastapi_app:app --reload

dev:  ## Start development environment
	@echo "Starting development environment..."
	@echo "Install: make dev-install"
	@echo "Run tests: make test"
	@echo "Run example: make run-example"
