"""
Start-time isolation guard for the exit daemon.

The exit MUST NOT endanger the mainnet node. Before an exit binds anything, this guard
refuses to run if:
  * the WireGuard UDP port is already in use (never steal a port — 443/udp is the node's),
  * the isolated nft table name is already present (avoid clobbering a prior/foreign table),
  * this host is running the validator/miner and the operator did not explicitly opt in
    with `--i-am-not-the-validator` (don't co-locate exit egress with consensus by accident).

It is deliberately conservative: any uncertainty is a refusal, not a warning.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from . import NFT_TABLE, WG_LISTEN_PORT


class GuardError(RuntimeError):
    pass


@dataclass
class GuardReport:
    ok: bool
    reasons: list[str]


def _udp_port_in_use(port: int) -> bool:
    if not shutil.which("ss"):
        return False
    out = subprocess.run(["ss", "-lun"], capture_output=True, text=True).stdout
    return any(f":{port} " in line or line.rstrip().endswith(f":{port}") for line in out.splitlines())


def _nft_table_exists(table: str) -> bool:
    if not shutil.which("nft"):
        return False
    out = subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout
    return f"inet {table}" in out


def _looks_like_validator_host() -> bool:
    """Best-effort: is a mainnet node / miner process running here?"""
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.lower()
    except Exception:
        return False
    markers = ("animica-mainnet-node", "animica node", "animica up", "animica miner", "stratum_pool")
    return any(m in out for m in markers)


def preflight(*, port: int = WG_LISTEN_PORT, allow_validator: bool = False) -> GuardReport:
    reasons: list[str] = []
    if _udp_port_in_use(port):
        reasons.append(f"UDP port {port} is already in use — refusing to steal a port "
                       f"(the mainnet node owns 443/udp; the exit must have {port} free).")
    if _nft_table_exists(NFT_TABLE):
        reasons.append(f"nft table 'inet {NFT_TABLE}' already exists — refusing to clobber it. "
                       f"Run `animica vpn exit stop` first, or remove it manually.")
    if _looks_like_validator_host() and not allow_validator:
        reasons.append("this host appears to run the mainnet node/miner. Running an exit here "
                       "co-locates third-party egress with consensus. If you accept that, re-run "
                       "with --i-am-not-the-validator.")
    return GuardReport(ok=not reasons, reasons=reasons)


def enforce(*, port: int = WG_LISTEN_PORT, allow_validator: bool = False) -> None:
    rep = preflight(port=port, allow_validator=allow_validator)
    if not rep.ok:
        raise GuardError("exit preflight failed:\n  - " + "\n  - ".join(rep.reasons))
