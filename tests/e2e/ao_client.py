"""Minimal AO API client for the dispatch e2e tests.

Talks to the Automation Orchestrator API of an aap-dev deployment
(``make AO_EXECUTION_PLANE=true ao-operator``) with plain HTTP: log in as
admin, create a project + workflow, run it, and poll the execution to a
terminal status. The assertions in the tests then read the activities'
business results — the same predicates the repository already trusts in
``make sdk-plugin-run``.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class AoApi:
    """A tiny synchronous client for the subset of the AO API the e2e needs."""

    def __init__(self, base_url: str, verify: bool | str = False) -> None:
        self._client = httpx.Client(base_url=base_url, verify=verify, timeout=30)

    def login(self, username: str, password: str) -> None:
        response = self._client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        self._client.headers["Authorization"] = f"Bearer {token}"

    def create_project(self, name: str) -> uuid.UUID:
        response = self._client.post("/api/v1/projects", json={"name": name})
        response.raise_for_status()
        return uuid.UUID(response.json()["id"])

    def create_workflow(
        self, name: str, definition: dict[str, Any], project_id: uuid.UUID
    ) -> uuid.UUID:
        response = self._client.post(
            "/api/v1/workflows",
            json={
                "name": name,
                "description": f"e2e: {name}",
                "workflow_definition": definition,
                "project_id": str(project_id),
            },
        )
        response.raise_for_status()
        return uuid.UUID(response.json()["id"])

    def create_execution(
        self, workflow_id: uuid.UUID, trigger_node_id: str = "trigger"
    ) -> uuid.UUID:
        response = self._client.post(
            "/api/v1/executions",
            json={"workflow_id": str(workflow_id), "trigger_node_id": trigger_node_id},
        )
        response.raise_for_status()
        return uuid.UUID(response.json()["id"])

    def get_execution(self, execution_id: uuid.UUID) -> dict[str, Any]:
        response = self._client.get(
            f"/api/v1/executions/{execution_id}", params={"include": "activities"}
        )
        response.raise_for_status()
        return response.json()

    def wait_for_terminal(
        self, execution_id: uuid.UUID, timeout_seconds: int = 120
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            execution = self.get_execution(execution_id)
            if execution["status"] in _TERMINAL_STATUSES:
                return execution
            if time.monotonic() >= deadline:
                message = f"Execution {execution_id} did not finish within {timeout_seconds}s"
                raise TimeoutError(message)
            time.sleep(1)

    def close(self) -> None:
        self._client.close()


def script_workflow(name: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a workflow definition chaining the given script nodes sequentially."""

    edges: list[dict[str, str]] = [{"from": "trigger", "to": nodes[0]["id"]}]
    for upstream, downstream in zip(nodes, nodes[1:], strict=False):
        edges.append({"from": upstream["id"], "to": downstream["id"]})
    return {
        "name": name,
        "schema_version": "2.0.0",
        "triggers": [{"id": "trigger", "type": "manual_trigger", "parameters": {}}],
        "nodes": nodes,
        "edges": edges,
    }


def admin_password_from_env() -> str:
    """Read the admin password from the environment or a password file."""

    password = os.environ.get("AO_ADMIN_PASSWORD", "")
    if password:
        return password
    path = os.environ.get(
        "AO_ADMIN_PASSWORD_FILE", os.path.expanduser("~/.aap-dev/ao-admin-password")
    )
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()
