#!/usr/bin/env python3
"""Simple off-chain relayer that listens for PayoutRequested events and calls token contract.

This is a minimal skeleton for local/dev use. It polls an RPC endpoint for new events
and, when it finds a PayoutRequested event, calls the configured token contract to
transfer/mint tokens to the worker.

In production, replace polling with an event subscription (WS) or indexer-backed queue.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict

import requests


class RpcClient:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    def post(self, method: str, params: Any):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        r = requests.post(self.rpc_url, json=payload)
        r.raise_for_status()
        return r.json()

    def get_logs(self, from_block: int, to_block: int):
        # Example RPC method: rpc_getLogs
        return self.post(
            "rpc_getLogs", {"from_block": from_block, "to_block": to_block}
        )

    def call_contract(self, contract_address: str, action: str, params: Dict[str, Any]):
        return self.post(
            "rpc_call_contract",
            {"address": contract_address, "action": action, "params": params},
        )


class PayoutRelayer:
    def __init__(self, rpc_url: str, token_contract: str):
        self.rpc = RpcClient(rpc_url)
        self.token_contract = token_contract
        self.last_block = 0

    def poll_once(self):
        # Poll logs from last_block to head
        head = self.rpc.post("chain.getHead", {})
        head_block = head.get("result", {}).get("height", self.last_block)
        if head_block <= self.last_block:
            return
        # Get logs (use RPC post for compatibility with test mocks)
        logs = self.rpc.post(
            "rpc_getLogs", {"from_block": self.last_block + 1, "to_block": head_block}
        )
        # Expect logs in logs['result'] list with entries containing 'event' name and data
        for ev in logs.get("result", []):
            if ev.get("event") == "PayoutRequested":
                job_id = ev.get("data", [])[0]
                worker_id = ev.get("data", [])[1]
                amount = ev.get("data", [])[2]
                token_addr = ev.get("data", [])[3]
                print(
                    f"PayoutRequested: job={job_id} worker={worker_id} amount={amount} token={token_addr}"
                )
                # Perform payout via token contract (assumes role_mint or similar)
                try:
                    # Use generic post RPC to call contract to be compatible with test mocks
                    res = self.rpc.post(
                        "rpc_call_contract",
                        {
                            "address": self.token_contract,
                            "action": "role_mint",
                            "params": {
                                "caller": "relayer",
                                "to": worker_id,
                                "amount": amount,
                            },
                        },
                    )
                    print("Payout executed:", res)
                except Exception as e:
                    print("Failed to execute payout:", e)
        self.last_block = head_block

    def run_loop(self, poll_interval: int = 5):
        while True:
            try:
                self.poll_once()
            except Exception as e:
                print("Relayer error:", e)
            time.sleep(poll_interval)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--rpc", default="http://127.0.0.1:8545")
    p.add_argument("--token-contract", required=True)
    args = p.parse_args()
    r = PayoutRelayer(args.rpc, args.token_contract)
    r.run_loop()
