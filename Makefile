.DEFAULT_GOAL := help

UV ?= uv
UV_RUN ?= $(UV) run --locked --no-sync
DOCKER ?= docker
IMAGE_NAME ?= dbtalk
IMAGE_TAG ?= latest
IMAGE := $(IMAGE_NAME):$(IMAGE_TAG)
BINARY_NAME ?= dbtalk
BINARY_DIST_DIR ?= dist
BINARY_BUILD_DIR ?= build/pyinstaller
CHECK_FIX := $(filter 1 true yes,$(fix))
RUFF_CHECK_ARGS :=
RUFF_FORMAT_ARGS := --check
TEST_COV_ARGS :=

ifeq ($(OS),Windows_NT)
PYINSTALLER_DATA_SEPARATOR := ;
PROJECT_ROOT := $(shell cygpath -w "$(CURDIR)")
else
PYINSTALLER_DATA_SEPARATOR := :
PROJECT_ROOT := $(CURDIR)
endif

ifneq ($(CHECK_FIX),)
RUFF_CHECK_ARGS := --fix
RUFF_FORMAT_ARGS :=
endif

ifneq ($(filter 1 true yes,$(cov)),)
TEST_COV_ARGS := --cov=dbtalk --cov-report=term-missing:skip-covered --cov-report=html
endif

.PHONY: help deps install check test version release binary release-image

help: ## Show available targets.
	@echo "Usage: make <target> [IMAGE_NAME=registry/dbtalk] [IMAGE_TAG=1.0.0]"
	@echo ""
	@echo "Targets:"
	@echo "  deps          Sync locked dependencies and install the project"
	@echo "  install       Install the CLI and synchronize agent plugins"
	@echo "  check         Run Ruff and Mypy quality checks (fix=1 enables fixes)"
	@echo "  test          Run unit tests (cov=1 enables coverage reports)"
	@echo "  version       Calculate the Git-derived version (apply=1 updates version files)"
	@echo "  release       Build source and wheel distributions"
	@echo "  binary        Build a standalone executable"
	@echo "  release-image Build the runtime container image"

deps: ## Sync locked development dependencies and install the project.
	$(UV) sync --all-groups --locked

install: ## Install the CLI and synchronize agent plugins.
	$(UV) run --locked python scripts/install.py plugin check
	$(UV) tool install --editable . --force
	dbtalk --version
	$(UV) run --locked python scripts/install.py plugin apply

check: ## Run Ruff and Mypy checks without tests; use fix=1 to fix Ruff issues.
	$(UV_RUN) ruff format $(RUFF_FORMAT_ARGS) src tests scripts
	$(UV_RUN) ruff check $(RUFF_CHECK_ARGS) src tests scripts
	$(UV_RUN) mypy src tests scripts

test: ## Run unit tests; use cov=1 to collect coverage.
	$(UV_RUN) pytest $(TEST_COV_ARGS)

version: ## Calculate the Git-derived project version; use apply=1 to update version files.
ifeq ($(apply),1)
	$(UV_RUN) python scripts/version_calc.py --no-dry-run
else
	$(UV_RUN) python scripts/version_calc.py
endif

release: ## Build source and wheel distributions.
	$(UV) build

binary: ## Build a standalone executable with PyInstaller.
	$(UV_RUN) pyinstaller --noconfirm --clean --onefile --name $(BINARY_NAME) --add-data "$(PROJECT_ROOT)/dbtalk.yaml$(PYINSTALLER_DATA_SEPARATOR)." --distpath $(BINARY_DIST_DIR) --workpath $(BINARY_BUILD_DIR) --specpath $(BINARY_BUILD_DIR) src/dbtalk/__main__.py

release-image: ## Build the runtime container image.
	$(DOCKER) build --tag $(IMAGE) .
