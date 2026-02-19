"""Debug bundle collector for Animica Studio.

Collects diagnostic information from the running application and formats it
as a human-readable multi-section text string suitable for clipboard export.

Nothing secret (private keys, passwords) is ever included.
"""

from __future__ import annotations

import importlib.metadata
import logging
import platform
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from animica_studio.services.diagnostics import Diagnostics
    from animica_studio.services.wallet_service import WalletService
    from animica_studio.storage.config import Config

log = logging.getLogger(__name__)

_SECTION_SEP = "\n" + "=" * 60 + "\n"
_MAX_LOG_LINES = 200  # number of recent log lines to include in the debug bundle


def _safe_app_version() -> str:
    try:
        return importlib.metadata.version("animica-studio")
    except Exception:  # noqa: BLE001
        return "unknown"


def _section(title: str, body: str) -> str:
    return f"{_SECTION_SEP}{title}\n{'-' * len(title)}\n{body.strip()}\n"


def collect_debug_bundle(
    config: "Config",
    diagnostics: "Diagnostics | None" = None,
    wallet_service: "WalletService | None" = None,
    last_head: Any = None,
    last_chain_id: int | None = None,
) -> str:
    """Collect and return a debug bundle as a multi-section plain-text string.

    Parameters
    ----------
    config:
        The active application config (secrets are stripped).
    diagnostics:
        The diagnostics service to pull recent events and log lines from.
    wallet_service:
        Optional wallet service to pull account addresses and pending txs.
    last_head:
        Last known chain head (number, hash, ts).
    last_chain_id:
        Last observed chain ID from RPC.
    """
    parts: list[str] = []
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ----------------------------------------------------------------
    # 1. App info
    # ----------------------------------------------------------------
    app_info_lines = [
        f"animica-studio version : {_safe_app_version()}",
        f"Python                 : {sys.version.split()[0]}",
        f"Platform               : {platform.platform()}",
        f"Report generated       : {now_ts}",
    ]
    parts.append(_section("App Info", "\n".join(app_info_lines)))

    # ----------------------------------------------------------------
    # 2. Active profile (sanitized — no keys)
    # ----------------------------------------------------------------
    profile_lines: list[str] = []
    try:
        from animica_studio.models.profile_models import RpcProfile  # noqa: PLC0415

        active_id = config.active_profile_id
        active_profile: RpcProfile | None = None
        for d in config.rpc_profiles:
            if isinstance(d, dict) and d.get("id") == active_id:
                active_profile = RpcProfile.from_dict(d)
                break

        if active_profile:
            profile_lines += [
                f"Name      : {active_profile.name}",
                f"Type      : {active_profile.type}",
                f"RPC URL   : {active_profile.rpc_url}",
                f"Chain ID  : {active_profile.chain_id_expected}",
            ]
        else:
            profile_lines.append("(no active RPC profile)")

        # Chain ID discrepancy
        if last_chain_id is not None and active_profile:
            expected = active_profile.chain_id_expected
            match = "✓ match" if last_chain_id == expected else f"✗ mismatch (expected {expected})"
            profile_lines.append(f"Chain ID (actual) : {last_chain_id} — {match}")

    except Exception as exc:  # noqa: BLE001
        profile_lines.append(f"(error reading profile: {exc})")

    parts.append(_section("Active Profile", "\n".join(profile_lines)))

    # ----------------------------------------------------------------
    # 3. Last head
    # ----------------------------------------------------------------
    head_lines: list[str] = []
    if last_head is not None:
        try:
            head_lines += [
                f"Height    : {getattr(last_head, 'number', '?')}",
                f"Hash      : {getattr(last_head, 'hash', '?')}",
                f"Timestamp : {getattr(last_head, 'timestamp', '?')}",
            ]
        except Exception as exc:  # noqa: BLE001
            head_lines.append(f"(error reading head: {exc})")
    else:
        head_lines.append("(no head data available)")
    parts.append(_section("Last Known Head", "\n".join(head_lines)))

    # ----------------------------------------------------------------
    # 4. RPC discover methods
    # ----------------------------------------------------------------
    discover_lines: list[str] = []
    try:
        from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

        active_id2 = config.active_profile_id
        rpc_url: str | None = None
        for d in config.rpc_profiles:
            if isinstance(d, dict) and d.get("id") == active_id2:
                rpc_url = d.get("rpc_url") or d.get("node_rpc_url")
                break
        if not rpc_url and config.profiles:
            rpc_url = config.profiles[0].rpc_url

        if rpc_url:
            try:
                with RpcClient(rpc_url, connect_timeout=2.0, read_timeout=5.0, max_retries=1) as c:
                    disc = c.discover()
                methods_raw = disc.get("methods", [])
                names = []
                for m in methods_raw[:50]:
                    if isinstance(m, dict):
                        names.append(m.get("name", str(m)))
                    else:
                        names.append(str(m))
                discover_lines.append(f"Methods ({len(methods_raw)} total):")
                discover_lines += [f"  {n}" for n in names]
                if len(methods_raw) > 50:
                    discover_lines.append(f"  … and {len(methods_raw) - 50} more")
            except Exception as exc:  # noqa: BLE001
                discover_lines.append(f"(discover failed: {exc})")
        else:
            discover_lines.append("(no RPC URL)")
    except Exception as exc:  # noqa: BLE001
        discover_lines.append(f"(error: {exc})")
    parts.append(_section("RPC Discover (method names)", "\n".join(discover_lines)))

    # ----------------------------------------------------------------
    # 5. Wallet accounts
    # ----------------------------------------------------------------
    wallet_lines: list[str] = []
    if wallet_service is not None:
        try:
            accounts = wallet_service.list_accounts()
            if accounts:
                for acc in accounts:
                    wallet_lines.append(f"  [{acc.label}] {acc.address}")
            else:
                wallet_lines.append("(no accounts)")
        except Exception as exc:  # noqa: BLE001
            wallet_lines.append(f"(error: {exc})")

        try:
            ptxs = wallet_service.list_pending_txs()
            wallet_lines.append(f"\nPending/sent txs ({len(ptxs)} total):")
            for ptx in ptxs[:20]:
                hash_str = ptx.tx_hash or "(no hash)"
                wallet_lines.append(f"  [{ptx.status}] {hash_str}")
            if len(ptxs) > 20:
                wallet_lines.append(f"  … and {len(ptxs) - 20} more")
        except Exception as exc:  # noqa: BLE001
            wallet_lines.append(f"(pending txs error: {exc})")
    else:
        wallet_lines.append("(wallet service not available)")
    parts.append(_section("Wallet Accounts", "\n".join(wallet_lines)))

    # ----------------------------------------------------------------
    # 6. Diagnostics events
    # ----------------------------------------------------------------
    diag_lines: list[str] = []
    if diagnostics is not None:
        try:
            events = diagnostics.get_events(last_n=50)
            if events:
                for ev in events:
                    diag_lines.append(
                        f"  [{ev.level}] [{ev.source}] {ev.message}"
                    )
            else:
                diag_lines.append("(no events)")
        except Exception as exc:  # noqa: BLE001
            diag_lines.append(f"(error: {exc})")
    else:
        diag_lines.append("(diagnostics service not available)")
    parts.append(_section("Diagnostics (last 50 events)", "\n".join(diag_lines)))

    # ----------------------------------------------------------------
    # 7. Recent log lines
    # ----------------------------------------------------------------
    log_lines_out: list[str] = []
    if diagnostics is not None:
        try:
            lines = diagnostics.get_recent_logs(last_n=_MAX_LOG_LINES)
            log_lines_out += lines if lines else ["(no log lines)"]
        except Exception as exc:  # noqa: BLE001
            log_lines_out.append(f"(error: {exc})")
    else:
        log_lines_out.append("(diagnostics service not available)")
    parts.append(_section(f"Recent Log Lines (last {_MAX_LOG_LINES})", "\n".join(log_lines_out)))

    return "".join(parts)
