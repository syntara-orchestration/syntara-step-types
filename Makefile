NODES := http-request script
CONTAINER_ENGINE ?= podman
REGISTRY ?= localhost
TAG ?= dev
NODE ?= http-request
UV ?= uv
SOURCE_URL ?= https://github.com/syntara-orchestration/syntara-step-types
VCS_REF ?= $(shell git rev-parse HEAD)

.PHONY: install
install:
	$(UV) sync --frozen --all-packages --group dev

.PHONY: lint format
lint:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .

format:
	$(UV) run --frozen ruff format .

.PHONY: typecheck
typecheck:
	$(UV) run --frozen --all-packages mypy --config-file pyproject.toml \
		$(addsuffix /src,$(NODES))

.PHONY: test
test:
	$(UV) run --frozen --all-packages pytest

.PHONY: test-ci
test-ci:
	$(UV) run --frozen --all-packages pytest \
		--junitxml=test-results/junit.xml \
		--cov \
		--cov-report=term-missing \
		--cov-report=xml:test-results/coverage.xml

.PHONY: check
check: lint typecheck test

.PHONY: node-images node-image
node-images:
	@set -e; for node in $(NODES); do $(MAKE) node-image NODE=$$node; done

node-image:
	@case " $(NODES) " in *" $(NODE) "*) ;; *) echo 'Unknown NODE: $(NODE)'; exit 2;; esac
	$(CONTAINER_ENGINE) build \
		--build-arg SOURCE_URL="$(SOURCE_URL)" \
		--build-arg VCS_REF="$(VCS_REF)" \
		-f $(NODE)/Containerfile \
		-t $(REGISTRY)/syntara-node-$(NODE):$(TAG) .

.PHONY: push-node-images push-node-image
push-node-images:
	@set -e; for node in $(NODES); do $(MAKE) push-node-image NODE=$$node; done

push-node-image:
	@case " $(NODES) " in *" $(NODE) "*) ;; *) echo 'Unknown NODE: $(NODE)'; exit 2;; esac
	$(CONTAINER_ENGINE) push $(REGISTRY)/syntara-node-$(NODE):$(TAG)
