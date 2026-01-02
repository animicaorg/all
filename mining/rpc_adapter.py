from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .share_submitter import JsonRpcClient


@dataclass
class RpcTemplateProvider:
    rpc_url: str
    proof_type: str = "sha256d"

    async def current_template(self) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_work)

    def _get_work(self) -> Optional[Dict[str, Any]]:
        client = JsonRpcClient(self.rpc_url)
        res = client.call("miner.getWork", [{"proofType": self.proof_type}])
        if isinstance(res, dict) and res.get("jobId"):
            return res
        return None
