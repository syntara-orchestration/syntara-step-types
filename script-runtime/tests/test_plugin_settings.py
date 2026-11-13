"""Validate the actual SDK resolution of both plugin workload configurations."""

from pathlib import Path

import pytest
from syntara_plugin.sdk.settings import load_settings


@pytest.mark.parametrize("step", ["script-python", "script-bash", "http-request"])
def test_workload_paths_resolve_from_settings_directory(step):
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(explicit_path=root / step / "syntara-plugin.yaml", manifest=None)
    workloads = settings.build.workloads
    assert workloads, f"{step}: no build.workloads entries"
    (identifier, workload) = next(iter(workloads.items()))
    assert identifier == step
    assert workload is not None
    assert workload.context == root
    assert workload.containerfile == root / step / "Containerfile"
    assert workload.containerfile.is_file()
    binding_manifest = settings.build.runtime_binding_manifest
    assert binding_manifest == root / step / "runtime-bindings.yaml"
    assert binding_manifest.is_file()


@pytest.mark.parametrize(
    ("step", "module_name"),
    [
        ("script-python", "syntara_node_script.python"),
        ("script-bash", "syntara_node_script.bash"),
        ("http-request", "syntara_node_http_request.__main__"),
    ],
)
def test_compiled_plugin_identity_matches_container_bootstrap(step, module_name):
    import importlib

    from syntara_plugin.runtime import step_registry_from_bindings
    from syntara_plugin.runtime.contract_validation import DriftCode, check_target_binding
    from syntara_plugin.sdk import BuildRequest, compile_workspace

    root = Path(__file__).resolve().parents[2]
    compiled = compile_workspace(
        BuildRequest(
            root_manifest=root / step / "plugin.yaml",
            image_bindings={f"{step}-runner": f"localhost/syntara/{step}@sha256:" + "a" * 64},
        )
    )
    target = compiled.descriptor["spec"]["targets"][0]
    bootstrap = importlib.import_module(module_name)
    # Use the production bootstrap, rather than inventing a binding from the expected identity.
    bindings = bootstrap.create_bindings(root / step / "manifest.yaml")
    registry = step_registry_from_bindings(bindings)
    assert target["identity"] in registry.step_revision_ids
    registry.resolve(target["identity"])
    report = check_target_binding(target, bindings.binding(target["identity"]))
    assert DriftCode.IDENTITY_MISMATCH not in {issue.code for issue in report.issues}
    assert report.passed(), report.errors
