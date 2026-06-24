"""Runner selection — local subprocess vs. remote fleet."""

from __future__ import annotations

from .client import BrokerClient
from .config import Mode, StudioConfig
from .runner_local import LocalRunner
from .runner_remote import RemoteRunner


def make_runner(config: StudioConfig):
    if config.mode == Mode.LOCAL:
        return LocalRunner(config)
    if config.mode == Mode.REMOTE:
        return RemoteRunner(config)

    # AUTO: prefer the fleet when a node is reachable AND a wallet is configured,
    # otherwise run locally so `animica studio run` always does *something*.
    reachable = False
    try:
        probe = BrokerClient(config.rpc_url, timeout=2.0)
        reachable = probe.reachable()
        probe.close()
    except Exception:
        reachable = False

    if reachable and config.wallet:
        return RemoteRunner(config)
    return LocalRunner(config)
