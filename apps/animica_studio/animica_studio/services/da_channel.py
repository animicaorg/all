from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .da_client import DaClient


@dataclass
class ChannelPointer:
    channel: str
    manifest_commitment: str
    metadata: dict[str, Any]


class DaChannelClient:
    def __init__(self, da: DaClient, namespace: int = 0) -> None:
        self.da = da
        self.namespace = namespace

    def publish_latest(self, channel: str, manifest_commitment: str, metadata: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "channel": channel,
            "manifest_commitment": manifest_commitment,
            "metadata": metadata,
        }
        return self.da.upload_json(payload, namespace=self.namespace)

    def read_pointer(self, commitment: str) -> ChannelPointer:
        blob = self.da.get_blob(commitment)
        payload = json.loads(blob.decode("utf-8"))
        return ChannelPointer(
            channel=str(payload.get("channel") or "ena-main/latest"),
            manifest_commitment=str(payload.get("manifest_commitment") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )
