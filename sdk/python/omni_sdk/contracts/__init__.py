"""
omni_sdk.contracts
==================

High-level helpers for working with Animica smart contracts.

Submodules
----------
- client   : Generic ABI-driven contract client (encode calls, decode returns).
- deployer : Helpers to deploy contract packages (manifest + code).
- events   : Event filter/decoder utilities (topics, logs → typed events).
- codegen  : Generate Python client stubs from an ABI.

Typical usage
-------------
    from omni_sdk.contracts import client, deployer, events, codegen

    # Load ABI and construct a generic client
    c = client.ContractClient(rpc=rpc, address="anim1...", abi=abi)

    # Call read-only method
    result = c.call("get", args=[])

    # Send a state-changing tx (build → sign → send handled inside helper if provided)
    tx = c.send("inc", args=[1], signer=signer)

    # Deploy a package
    addr, receipt = deployer.deploy_package(
        rpc=rpc, signer=signer, manifest=manifest, code=code_bytes
    )

    # Decode events from a receipt
    decoded = events.decode_receipt_events(abi, receipt["logs"])

    # Generate a typed client class from ABI (optional)
    code = codegen.emit_python_client(abi, class_name="CounterClient")


Notes
-----
This package re-exports its submodules as namespaces to keep API surface
stable. Import concrete classes/functions directly from the submodules for
type checking and IDE support.
"""

from __future__ import annotations

# Re-export submodule namespaces
from . import client as client
from . import deployer as deployer
from . import events as events
from . import codegen as codegen

__all__ = ["client", "deployer", "events", "codegen", "contracts"]


class ContractsAPI:
    """
    Ergonomic accessor that groups contract helpers.

    Example:
        from omni_sdk.contracts import contracts
        addr, rcpt = contracts.deployer.deploy_package(...)
        c = contracts.client.ContractClient(rpc, addr, abi)
        logs = contracts.events.decode_receipt_events(abi, rcpt["logs"])
    """

    @property
    def client(self):
        return client

    @property
    def deployer(self):
        return deployer

    @property
    def events(self):
        return events

    @property
    def codegen(self):
        return codegen


# Singleton namespace for convenience
contracts = ContractsAPI()
