NODES := http-request agent script aap-job aap-workflow
CONTAINER_ENGINE ?= podman
REGISTRY ?= localhost
TAG ?= migration-test
NODE ?= http-request
UV ?= uv
SOURCE_URL ?= https://github.com/syntara-orchestration/syntara-step-types
VCS_REF ?= $(shell git rev-parse HEAD)

.PHONY: install typecheck test node-images node-image push-node-images push-node-image manifests
install:
	$(UV) sync --frozen --all-packages --group dev

typecheck:
	$(UV) run --frozen --all-packages mypy --config-file pyproject.toml _protocol/src _shared/src http-request/src agent/src script/src aap-job/src aap-workflow/src

test:
	$(UV) run --frozen --all-packages pytest

.PHONY: lint format test-ci check check-generated sync-requirements
lint:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .

format:
	$(UV) run --frozen ruff format .

test-ci:
	$(UV) run --frozen --all-packages pytest --junitxml=test-results/junit.xml --cov --cov-report=term-missing --cov-report=xml:test-results/coverage.xml

check: lint typecheck test check-generated

sync-requirements:
	$(UV) export --frozen --all-packages --no-dev --no-editable --no-emit-workspace --no-header --output-file requirements.txt > /dev/null

check-generated:
	@set -eu; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	$(UV) run --frozen --all-packages python -m grpc_tools.protoc -I_protocol/src --python_out="$$tmp" --grpc_python_out="$$tmp" --mypy_out="$$tmp" --mypy_grpc_out="$$tmp" _protocol/src/syntara_node_protocol/node.proto; \
	for file in node_pb2.py node_pb2.pyi node_pb2_grpc.py node_pb2_grpc.pyi; do \
	  diff -u "_protocol/src/syntara_node_protocol/$$file" "$$tmp/syntara_node_protocol/$$file"; \
	done; \
	$(UV) export --frozen --all-packages --no-dev --no-editable --no-emit-workspace --no-header --output-file "$$tmp/requirements.txt" > /dev/null; \
	diff -u requirements.txt "$$tmp/requirements.txt"

node-images:
	@set -e; for node in $(NODES); do $(MAKE) node-image NODE=$$node; done

node-image:
	@case " $(NODES) " in *" $(NODE) "*) ;; *) echo 'Unknown NODE'; exit 2;; esac
	$(CONTAINER_ENGINE) build --build-arg SOURCE_URL="$(SOURCE_URL)" --build-arg VCS_REF="$(VCS_REF)" -f $(NODE)/Containerfile -t $(REGISTRY)/syntara-node-$(NODE):$(TAG) .

push-node-images:
	@set -e; for node in $(NODES); do $(MAKE) push-node-image NODE=$$node; done

push-node-image:
	@case " $(NODES) " in *" $(NODE) "*) ;; *) echo 'Unknown NODE'; exit 2;; esac
	$(CONTAINER_ENGINE) push $(REGISTRY)/syntara-node-$(NODE):$(TAG)

.PHONY: smoke-images
smoke-images:
	CONTAINER_ENGINE=$(CONTAINER_ENGINE) REGISTRY=$(REGISTRY) TAG=$(TAG) $(UV) run --frozen --all-packages python tools/smoke_images.py

.PHONY: proto
proto:
	$(UV) run --frozen --all-packages python -m grpc_tools.protoc -I_protocol/src --python_out=_protocol/src --grpc_python_out=_protocol/src --mypy_out=_protocol/src --mypy_grpc_out=_protocol/src _protocol/src/syntara_node_protocol/node.proto
