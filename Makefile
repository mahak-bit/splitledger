.DEFAULT_GOAL := help
PYTHON ?= python

.PHONY: help install test lint format typecheck check run demo clean

help:  ## Show the available targets
	@grep -hE '^[a-z_-]+:.*## ' $(MAKEFILE_LIST) | sed -e 's/:.*## /  --  /'

install:  ## Install the package with dev extras
	$(PYTHON) -m pip install -e ".[dev]"

test:  ## Run the test suite with coverage
	$(PYTHON) -m pytest --cov --cov-report=term-missing

lint:  ## Lint and verify formatting
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:  ## Apply safe fixes and formatting
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

typecheck:  ## Run mypy in strict mode
	$(PYTHON) -m mypy

check: lint typecheck test  ## Everything CI runs

run:  ## Start the API with autoreload
	$(PYTHON) -m splitledger.cli serve --reload

demo:  ## Seed a demo ledger and print its settlement plan
	$(PYTHON) -m splitledger.cli demo

clean:  ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
