"""Fixtures for the dispatch e2e tests.

The suite runs against a live AO + Execution Plane deployment
(``make AO_EXECUTION_PLANE=true ao-operator`` from aap-dev, with a staged
syntara-step-types checkout providing the script-node image). Everything
gates on ``AO_API_BASE``: unset means "no deployment", and the tests skip
so the suite stays runnable in plain CI (unit + integration only).
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e.ao_client import AoApi, admin_password_from_env


def _ao_configured() -> bool:
    return bool(os.environ.get("AO_API_BASE"))


@pytest.fixture(scope="session")
def ao_api():
    if not _ao_configured():
        pytest.skip("AO_API_BASE must point at the AO API of a live aap-dev deployment")

    base = os.environ["AO_API_BASE"]
    verify = os.environ.get("AO_API_CA_FILE", "")
    api = AoApi(base, verify=verify or False)
    api.login("admin", admin_password_from_env())
    yield api
    api.close()


@pytest.fixture(scope="session")
def e2e_project_id(ao_api: AoApi) -> uuid.UUID:
    return ao_api.create_project(f"step-types-e2e-{uuid.uuid4().hex[:8]}")
