"""Validate the actual SDK resolution of both plugin workload configurations."""

from pathlib import Path

import pytest
from syntara_plugin.sdk.settings import load_settings


@pytest.mark.parametrize("step", ["script", "http-request"])
def test_workload_paths_resolve_from_settings_directory(step):
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(explicit_path=root / step / "syntara-plugin.yaml", manifest=None)
    workload = settings.build.workload
    assert workload is not None
    assert workload.context == root
    assert workload.containerfile == root / step / "Containerfile"
    assert workload.containerfile.is_file()
