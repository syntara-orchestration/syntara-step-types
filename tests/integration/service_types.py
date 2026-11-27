"""Types shared by integration fixtures and image-level tests."""

from __future__ import annotations

from dataclasses import dataclass

from syntara_plugin.runtime.protocol import container_pb2_grpc

TEST_BEARER_TOKEN = "image-callback-token"
AAP_TEST_TOKEN = "aap-image-callback-token"


@dataclass(frozen=True)
class ImageServices:
    http: container_pb2_grpc.ContainerServiceStub
    python: container_pb2_grpc.ContainerServiceStub
    bash: container_pb2_grpc.ContainerServiceStub
    aap_workflow: container_pb2_grpc.ContainerServiceStub
    upstream_url: str
    https_upstream_url: str
    aap_upstream_url: str
    aap_ca_certificate: str
    aap_container_name: str
    aap_state_url: str
