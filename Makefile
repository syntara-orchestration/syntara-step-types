NODES := http-request script-python script-bash
SDK_PLUGINS := http-request script-python script-bash
PYTHON_PACKAGES := http-request script-runtime
CONTAINER_ENGINE ?= podman
REGISTRY ?= localhost
TAG ?= dev
NODE ?= http-request
PLUGIN ?=
SDK_WORKLOAD_TAG ?= ci-$(shell date +%s)
UV ?= uv
PYTEST_ARGS ?=

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

.PHONY: test test-unit
test: test-unit

test-unit:
	$(UV) run --frozen --all-packages pytest -m "not integration" $(PYTEST_ARGS)

.PHONY: test-build
test-build:
	$(UV) run --frozen --all-packages python .github/scripts/build_test_images.py

.PHONY: test-integration
test-integration:
	$(UV) run --frozen --all-packages python .github/scripts/validate_test_build.py
	$(UV) run --frozen --all-packages pytest -m integration $(PYTEST_ARGS)

.PHONY: test-all
test-all:
	$(MAKE) test-unit
	$(MAKE) test-build
	$(MAKE) test-integration

.PHONY: test-clean
test-clean:
	rm -rf .test-artifacts test-results .coverage coverage.xml

.PHONY: test-ci
test-ci:
	mkdir -p test-results
	$(MAKE) test-unit PYTEST_ARGS="--junitxml=test-results/unit-junit.xml --cov --cov-report=term-missing --cov-report=xml:test-results/coverage.xml"

.PHONY: test-integration-ci
test-integration-ci:
	mkdir -p test-results
	$(MAKE) test-integration PYTEST_ARGS="--junitxml=test-results/integration-junit.xml -v"

.PHONY: check
check: lint typecheck test-unit

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
		--runtime-bindings runtime-bindings.yaml \
		--output "dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG).oci.tar" --json > "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-build.json"
	jq -e '(.pluginArtifactDigest | test("^sha256:[0-9a-f]{64}$$")) and (.images[0].reference | test("@sha256:[0-9a-f]{64}$$"))' "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-build.json"
	$(UV) run --frozen --all-packages --directory "$(PLUGIN)" syntara-plugin inspect "dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG).oci.tar" --json > "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-inspect.json"
	jq -e '.artifactDigest | test("^sha256:[0-9a-f]{64}$$")' "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-inspect.json"
	$(UV) run --frozen --all-packages python .github/scripts/assert_plugin_archive.py "$(PLUGIN)" \
		--build-result "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-build.json" \
		--inspect-result "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG)-inspect.json" \
		--plugin-archive "$(PLUGIN)/dist/$(PLUGIN)-$(SDK_WORKLOAD_TAG).oci.tar" \
		--artifact-root "$(PLUGIN)/dist"

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
	$(MAKE) sdk-plugin-build-inspect PLUGIN=$(NODE) SDK_WORKLOAD_TAG=$(SDK_WORKLOAD_TAG)
	@set -e; \
	image_archive="$$(jq -r '.images[] | select(.buildTarget == "$(NODE)") | .archive' "$(NODE)/dist/$(NODE)-$(SDK_WORKLOAD_TAG)-build.json")"; \
	test -n "$$image_archive"; \
	case "$$image_archive" in /*) ;; *) image_archive="$(NODE)/$$image_archive" ;; esac; \
	load_output="$$($(CONTAINER_ENGINE) load --input "$$image_archive")"; \
	image_reference="$$(printf '%s\n' "$$load_output" | sed -nE 's/^Loaded image(s)?: //p')"; \
	test -n "$$image_reference"; \
	$(CONTAINER_ENGINE) tag "$$image_reference" "$(REGISTRY)/syntara-node-$(NODE):$(TAG)"

.PHONY: push-node-images push-node-image
push-node-images:
	@set -e; for node in $(NODES); do $(MAKE) push-node-image NODE=$$node; done

push-node-image:
	@case " $(NODES) " in *" $(NODE) "*) ;; *) echo 'Unknown NODE: $(NODE)'; exit 2;; esac
	$(CONTAINER_ENGINE) push $(REGISTRY)/syntara-node-$(NODE):$(TAG)
