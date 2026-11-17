"""Types shared by integration fixtures and image-level tests."""

from __future__ import annotations

from dataclasses import dataclass

from syntara_plugin.runtime.protocol import container_pb2_grpc


@dataclass(frozen=True)
class ImageServices:
    http: container_pb2_grpc.ContainerServiceStub
    python: container_pb2_grpc.ContainerServiceStub
    bash: container_pb2_grpc.ContainerServiceStub
    upstream_url: str
