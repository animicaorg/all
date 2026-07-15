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


# Tri-state helpers: True = condition holds, False = provably does not, None = COULD NOT CHECK.
# Per the conservative posture, None is treated as a refusal by preflight — never a silent pass.

def _udp_port_in_use(port: int):
    if not shutil.which("ss"):
        return None
    out = subprocess.run(["ss", "-lun"], capture_output=True, text=True).stdout
    return any(f":{port} " in line or line.rstrip().endswith(f":{port}") for line in out.splitlines())


def _nft_table_exists(table: str):
    if not shutil.which("nft"):
        return None
    out = subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout
    return f"inet {table}" in out


def _looks_like_validator_host():
    """Best-effort: is a mainnet node / miner process running here? None if we can't enumerate."""
    if not shutil.which("ps"):
        return None
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.lower()
    except Exception:
        return None
    markers = ("animica-mainnet-node", "animica node", "animica up", "animica miner", "stratum_pool")
    return any(m in out for m in markers)


def preflight(*, port: int = WG_LISTEN_PORT, allow_validator: bool = False) -> GuardReport:
    reasons: list[str] = []

    used = _udp_port_in_use(port)
    if used is None:
        reasons.append(f"cannot verify UDP port {port} is free ('ss'/iproute2 not found) — refusing "
                       f"rather than risk stealing the node's port. Install iproute2.")
    elif used:
        reasons.append(f"UDP port {port} is already in use — refusing to steal a port "
                       f"(the mainnet node owns 443/udp; the exit must have {port} free).")

    exists = _nft_table_exists(NFT_TABLE)
    if exists is None:
        reasons.append("cannot verify the isolated nft table is absent ('nft'/nftables not found) — refusing. "
                       "Install nftables.")
    elif exists:
        reasons.append(f"nft table 'inet {NFT_TABLE}' already exists — refusing to clobber it. "
                       f"Run `animica vpn exit stop` first, or remove it manually.")

    if not allow_validator:
        vh = _looks_like_validator_host()
        if vh is None:
            reasons.append("cannot determine whether the mainnet node/miner runs on this host ('ps' unavailable) — "
                           "refusing to co-locate exit egress with consensus by accident. If this host is NOT a "
                           "validator, re-run with --i-am-not-the-validator.")
        elif vh:
            reasons.append("this host appears to run the mainnet node/miner. Running an exit here "
                           "co-locates third-party egress with consensus. If you accept that, re-run "
                           "with --i-am-not-the-validator.")
    return GuardReport(ok=not reasons, reasons=reasons)


def enforce(*, port: int = WG_LISTEN_PORT, allow_validator: bool = False) -> None:
    rep = preflight(port=port, allow_validator=allow_validator)
    if not rep.ok:
        raise GuardError("exit preflight failed:\n  - " + "\n  - ".join(rep.reasons))
