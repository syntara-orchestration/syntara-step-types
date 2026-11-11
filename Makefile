NODES := http-request script-python script-bash
SDK_PLUGINS := http-request script-python script-bash
PYTHON_PACKAGES := http-request script-runtime
CONTAINER_ENGINE ?= podman
REGISTRY ?= localhost
TAG ?= dev
NODE ?= http-request
PLUGIN ?=
SDK_WORKLOAD_TAG ?= ci-$(shell date +%s)
CONTRACTS_VERSION ?=
CONTRACTS_VERSION_BUILD_ARG = $(if $(strip $(CONTRACTS_VERSION)),--build-arg CONTRACTS_VERSION="$(CONTRACTS_VERSION)",)
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
		$(addsuffix /src,$(PYTHON_PACKAGES))

.PHONY: test
test:
	$(UV) run --frozen --all-packages pytest -m "not integration"

.PHONY: test-integration
test-integration:
	$(UV) run --frozen --all-packages pytest -m integration

.PHONY: test-all
test-all:
	$(UV) run --frozen --all-packages pytest

.PHONY: test-ci
test-ci:
	$(UV) run --frozen --all-packages pytest -m "not integration" \
		--junitxml=test-results/junit.xml \
		--cov \
		--cov-report=term-missing \
		--cov-report=xml:test-results/coverage.xml

.PHONY: test-integration-ci
test-integration-ci:
	$(UV) run --frozen --all-packages pytest -m integration \
		--junitxml=test-results/integration-junit.xml \
		-v

.PHONY: check
check: lint typecheck test

.PHONY: sdk-plugin-check
sdk-plugin-check:
	@case "$(PLUGIN)" in \
		http-request|script-python|script-bash) ;; \
		*) echo 'Set PLUGIN to one of: $(SDK_PLUGINS)'; exit 2 ;; \
	esac

.PHONY: sdk-plugin-validate
sdk-plugin-validate: sdk-plugin-check
	$(UV) run --frozen --all-packages --directory "$(PLUGIN)" syntara-plugin validate --json | jq -e '.ok == true'

.PHONY: sdk-plugin-run
sdk-plugin-run: sdk-plugin-check
	@case "$(PLUGIN)" in \
		http-request) filter='.ok == true and .output.status_code == 200' ;; \
		script-python) filter='.ok == true and .output.return_code == 0 and .output.stdout_json.result == 42' ;; \
		script-bash) filter='.ok == true and .output.return_code == 0 and .output.stdout == "hello from bash\n"' ;; \
	esac; \
	$(UV) run --frozen --all-packages --directory "$(PLUGIN)" syntara-plugin run --mode source --input-file example.json --json | jq -e "$$filter"

.PHONY: sdk-plugin-build-inspect
sdk-plugin-build-inspect: sdk-plugin-check
	@mkdir -p "$(PLUGIN)/dist"
	$(UV) run --frozen --all-packages --directory "$(PLUGIN)" syntara-plugin build \
		--output "dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG).oci.tar" --json > "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-build.json"
	jq -e '(.pluginArtifactDigest | test("^sha256:[0-9a-f]{64}$$")) and (.images[0].reference | test("@sha256:[0-9a-f]{64}$$"))' "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-build.json"
	$(UV) run --frozen --all-packages --directory "$(PLUGIN)" syntara-plugin inspect "dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG).oci.tar" --json > "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-inspect.json"
	jq -e '.artifactDigest | test("^sha256:[0-9a-f]{64}$$")' "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-inspect.json"
	$(UV) run --frozen --all-packages python .github/scripts/assert_plugin_archive.py "$(PLUGIN)" "$(SDK_WORKLOAD_TAG)"

# Assert the frozen output fields in docs/compatibility-matrix.md against each
# plugin manifest's spec.output.required.
.PHONY: sdk-plugin-drift
sdk-plugin-drift:
	$(UV) run --frozen --all-packages pytest tests/test_matrix_drift.py -q

.PHONY: sdk-plugin-ci
sdk-plugin-ci: sdk-plugin-validate sdk-plugin-run sdk-plugin-build-inspect sdk-plugin-drift

.PHONY: node-images node-image
node-images:
	@set -e; for node in $(NODES); do $(MAKE) node-image NODE=$$node; done

node-image:
	@case " $(NODES) " in *" $(NODE) "*) ;; *) echo 'Unknown NODE: $(NODE)'; exit 2;; esac
	$(CONTAINER_ENGINE) build \
		$(CONTRACTS_VERSION_BUILD_ARG) \
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
