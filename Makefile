NODES :=
UV ?= uv

.PHONY: install typecheck test
install:
	$(UV) sync --frozen --all-packages --group dev

typecheck:
	$(UV) run --frozen --all-packages mypy --config-file pyproject.toml _protocol/src _shared/src $(addsuffix /src,$(NODES))

test:
	$(UV) run --frozen --all-packages pytest

.PHONY: lint format test-ci check check-generated
lint:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .

format:
	$(UV) run --frozen ruff format .

test-ci:
	$(UV) run --frozen --all-packages pytest --junitxml=test-results/junit.xml --cov --cov-report=term-missing --cov-report=xml:test-results/coverage.xml

check: lint typecheck test check-generated

check-generated:
	@set -eu; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	$(UV) run --frozen --all-packages python -m grpc_tools.protoc -I_protocol/src --python_out="$$tmp" --grpc_python_out="$$tmp" --mypy_out="$$tmp" --mypy_grpc_out="$$tmp" _protocol/src/syntara_node_protocol/node.proto; \
	for file in node_pb2.py node_pb2.pyi node_pb2_grpc.py node_pb2_grpc.pyi; do \
	  diff -u "_protocol/src/syntara_node_protocol/$$file" "$$tmp/syntara_node_protocol/$$file"; \
	done

.PHONY: proto
proto:
	$(UV) run --frozen --all-packages python -m grpc_tools.protoc -I_protocol/src --python_out=_protocol/src --grpc_python_out=_protocol/src --mypy_out=_protocol/src --mypy_grpc_out=_protocol/src _protocol/src/syntara_node_protocol/node.proto
