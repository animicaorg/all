"""Shared status aggregation and control helpers for Studio product flows."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from animica_studio.models.profile_models import ProfileType, RpcProfile
from animica_studio.models.studio_models import (
    FeatureSummary,
    NodeSummary,
    OnboardingProbe,
    StatusIssue,
    StudioSnapshot,
    SyncSummary,
    WalletEntrySummary,
    WalletSummary,
)
from animica_studio.models.wallet_models import shorten_address
from animica_studio.services.activity_store import ActivityStore
from animica_studio.services.balance_service import BalanceResult as RpcBalanceResult
from animica_studio.services.da_dir_usage_service import DaDirUsageService
from animica_studio.services.da_status_service import DaStatusService
from animica_studio.services.error_format import safe_str
from animica_studio.services.job_runner import run_cli_blocking
from animica_studio.services.process_manager import ProcessManager
from animica_studio.services.rpc_client import RpcClient, RpcResponseError
from animica_studio.services.settings_service import SettingsService
from animica_studio.services.wallet_repository import WalletRecord, WalletRepository
from animica_studio.services.wallet_service import WalletService
from animica_studio.storage.config import Config
from animica_studio.util.paths import app_data_dir

log = logging.getLogger(__name__)


@dataclass
class ServiceActionResult:
    ok: bool
    summary: str
    details: str = ""
    payload: dict[str, Any] | None = None


def _safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 16) if text.startswith("0x") else int(text)
    except ValueError:
        return None


class StudioStatusService:
    def __init__(self, config: Config, settings_service: SettingsService | None = None) -> None:
        self._config = config
        self._settings = settings_service or SettingsService(config)
        self._wallet_repo = WalletRepository()
        self._wallet_service = WalletService(config)
        self._da_status = DaStatusService(config)
        self._da_usage = DaDirUsageService()
        self._lock = threading.Lock()

    def collect_snapshot(self) -> StudioSnapshot:
        with self._lock:
            profile = self._active_profile()
            wallets = self._wallet_repo.load_wallets()
            wallet_summary = self._collect_wallet_summary(profile, wallets)
            node_summary = self._collect_node_summary(profile)
            snapshot = StudioSnapshot(
                profile_name=profile.name,
                network_name=self._settings.detect_network(profile).replace("-", " ").title(),
                rpc_url=profile.effective_rpc_url(),
                wallet=wallet_summary,
                node=node_summary,
                mining=self._collect_mining_summary(wallet_summary),
                ena=self._collect_ena_summary(),
                aicf=self._collect_aicf_summary(node_summary),
                da=self._collect_da_summary(node_summary),
            )
            snapshot.issues = self._collect_issues(snapshot, wallets)
            return snapshot

    def collect_node_summary(self) -> NodeSummary:
        with self._lock:
            profile = self._active_profile()
            return self._collect_node_summary(profile)

    def probe_onboarding(self) -> OnboardingProbe:
        snapshot = self.collect_snapshot()
        issues = list(snapshot.issues)
        return OnboardingProbe(
            has_profile=bool(self._config.rpc_profiles),
            has_wallet=snapshot.wallet.wallet_count > 0,
            rpc_reachable=snapshot.node.rpc_reachable,
            node_running=snapshot.node.running,
            sync_complete=snapshot.node.sync.state in {"SYNCHRONIZED", "SYNCED", "NEAR_TIP"},
            selected_network=self._settings.detect_network(self._active_profile()),
            wallet_count=snapshot.wallet.wallet_count,
            issues=issues,
        )

    def active_profile(self) -> RpcProfile:
        return self._active_profile()

    def create_wallet(self, label: str, sig_scheme: str, *, allow_insecure_fallback: bool = False):
        return self._wallet_service.create_wallet(
            label,
            sig_scheme,
            allow_insecure_fallback=allow_insecure_fallback,
        )

    def import_wallet_store(self, source_path: str | Path) -> ServiceActionResult:
        try:
            count, dst = self._settings.import_wallet_store(source_path)
        except Exception as exc:  # noqa: BLE001
            return ServiceActionResult(False, "Wallet import failed.", safe_str(exc))
        ActivityStore.instance().record_wallet_load("Imported wallet store", ok=True, detail=str(dst))
        return ServiceActionResult(True, f"Imported {count} wallet(s).", f"Saved to {dst}")

    def refresh_wallet_selection(self, address: str | None) -> None:
        self._settings.set_last_selected_wallet(address)

    def test_rpc(self, rpc_url: str) -> ServiceActionResult:
        try:
            client = RpcClient(rpc_url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
            try:
                head = client.get_head()
                try:
                    chain_id = client.get_chain_id()
                except Exception:
                    chain_id = None
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001
            return ServiceActionResult(False, "RPC is not reachable.", safe_str(exc))
        chain_label = f" on chain {chain_id}" if chain_id is not None else ""
        return ServiceActionResult(True, f"RPC is reachable{chain_label}.", f"Head height: {head.number}")

    def start_node(self) -> ServiceActionResult:
        profile = self._active_profile()
        if profile.type != ProfileType.LOCAL_NODE:
            return ServiceActionResult(False, "This profile is not configured for a managed local node.")
        result = self._process_manager(profile).start()
        ok = bool(result.get("running") or result.get("rpc_reachable"))
        return ServiceActionResult(ok, "Node start requested." if ok else "Node did not start.", safe_str(result))

    def stop_node(self) -> ServiceActionResult:
        result = self._process_manager(self._active_profile()).stop()
        ok = not bool(result.get("running"))
        return ServiceActionResult(ok, "Node stopped." if ok else "Node stop did not complete.", safe_str(result))

    def restart_node(self) -> ServiceActionResult:
        result = self._process_manager(self._active_profile()).restart()
        ok = bool(result.get("running") or result.get("rpc_reachable"))
        return ServiceActionResult(ok, "Node restarted." if ok else "Node restart did not complete.", safe_str(result))

    def force_sync(self) -> ServiceActionResult:
        return self._run_cli_action(["sync", "force"], timeout_s=60)

    def bootstrap_node(self, network_key: str | None = None) -> ServiceActionResult:
        profile = self._active_profile()
        if profile.type != ProfileType.LOCAL_NODE:
            return ServiceActionResult(False, "Bootstrap is only available for local-node profiles.")
        key = network_key or self._settings.detect_network(profile)
        if key == "custom":
            return ServiceActionResult(False, "Bootstrap needs a known network preset. Choose Mainnet, Testnet, or Devnet.")
        return self._run_cli_action(["node", "bootstrap", "--network", key], timeout_s=120)

    def discover_snapshot(self) -> ServiceActionResult:
        return self._run_cli_action(["snapshot", "discover"], timeout_s=60)

    def sync_diagnostics_text(self) -> str:
        snapshot = self.collect_snapshot()
        lines = [
            f"Profile: {snapshot.profile_name}",
            f"RPC: {snapshot.rpc_url}",
            f"Node running: {snapshot.node.running}",
            f"RPC reachable: {snapshot.node.rpc_reachable}",
            f"Head height: {snapshot.node.head_number if snapshot.node.head_number is not None else 'unknown'}",
            f"Peers: {snapshot.node.peer_count if snapshot.node.peer_count is not None else 'unknown'}",
            f"Sync state: {snapshot.node.sync.state}",
            f"Sync detail: {snapshot.node.sync.detail or 'n/a'}",
        ]
        if snapshot.node.sync.stall_reason:
            lines.append(f"Stall reason: {snapshot.node.sync.stall_reason}")
        if snapshot.node.last_error:
            lines.append(f"Last error: {snapshot.node.last_error}")
        for issue in snapshot.issues:
            lines.append(f"{issue.level.upper()}: {issue.title} {issue.detail}".strip())
        return "\n".join(lines)

    def _active_profile(self) -> RpcProfile:
        active_id = self._config.active_profile_id
        for raw in list(self._config.rpc_profiles or []):
            if isinstance(raw, dict) and raw.get("id") == active_id:
                return RpcProfile.from_dict(raw)
        if self._config.rpc_profiles:
            return RpcProfile.from_dict(self._config.rpc_profiles[0])
        return RpcProfile.make_default_remote()

    def _process_manager(self, profile: RpcProfile) -> ProcessManager:
        data_dir = Path(profile.node_datadir).expanduser() if profile.node_datadir else app_data_dir()
        return ProcessManager(
            start_cmd=list(profile.node_start_cmd or ["animica", "node", "start"]),
            rpc_url=profile.node_rpc_url or profile.effective_rpc_url(),
            data_dir=data_dir,
            config=self._config,
        )

    def _collect_wallet_summary(self, profile: RpcProfile, wallets: list[WalletRecord]) -> WalletSummary:
        selected_address = self._settings.last_selected_wallet()
        wallet_rows: list[WalletEntrySummary] = []
        total_smallest = 0
        total_ok = False
        selected_label = ""
        selected_balance_text = "Unavailable"
        selected_balance_reason = ""

        if wallets and not selected_address:
            selected_address = wallets[0].address

        for index, wallet in enumerate(wallets):
            balance = self._load_wallet_balance(wallet.address, profile)
            wallet_rows.append(
                WalletEntrySummary(
                    label=wallet.label,
                    address=wallet.address,
                    sig_scheme=wallet.sig_scheme or "",
                    balance_text=balance.formatted or "Unavailable",
                    balance_ok=balance.ok,
                    balance_reason=balance.error_reason or "",
                )
            )
            if balance.ok and balance.amount_smallest is not None:
                total_smallest += int(balance.amount_smallest)
                total_ok = True
            if wallet.address == selected_address:
                selected_label = wallet.label
                selected_balance_text = balance.formatted or "Unavailable"
                selected_balance_reason = balance.error_reason or ""
            if index == 0 and not selected_label:
                selected_label = wallet.label

        total_balance_text = "—"
        if total_ok:
            total_balance_text = f"{total_smallest / 1_000_000_000:g} ANM"
        elif wallets:
            total_balance_text = "Unavailable"
        else:
            total_balance_text = "0 ANM"

        recent_txs = []
        for tx in self._wallet_service.list_pending_txs(selected_address)[:8]:
            recent_txs.append(
                {
                    "status": tx.status,
                    "amount": f"{tx.amount_wei / 1_000_000_000:g} ANM",
                    "to": shorten_address(tx.to_addr),
                    "hash": shorten_address(tx.tx_hash or ""),
                }
            )

        return WalletSummary(
            wallet_count=len(wallets),
            selected_address=selected_address,
            selected_label=selected_label,
            selected_balance_text=selected_balance_text,
            selected_balance_reason=selected_balance_reason,
            primary_address=selected_address or "",
            total_balance_text=total_balance_text,
            total_balance_ok=total_ok,
            explorer_ready=bool(profile.explorer_base_url),
            recent_txs=recent_txs,
            wallets=wallet_rows,
        )

    def _load_wallet_balance(self, address: str, profile: RpcProfile) -> RpcBalanceResult:
        cached = self._wallet_service.get_cached_balance(address)
        if cached and cached.error is None:
            return RpcBalanceResult(
                ok=True,
                amount_smallest=cached.balance_wei,
                formatted=cached.formatted,
                error_reason=None,
                source=str(cached.source or "cache"),
            )
        state = self._wallet_service.fetch_balance(address, profile.effective_rpc_url(), profile)
        return RpcBalanceResult(
            ok=state.error is None,
            amount_smallest=state.balance_wei,
            formatted=state.formatted,
            error_reason=state.error,
            source=str(state.source or "rpc"),
        )

    def _collect_node_summary(self, profile: RpcProfile) -> NodeSummary:
        pm_status = self._process_manager(profile).status()
        summary = NodeSummary(
            running=bool(pm_status.get("running")),
            rpc_reachable=bool(pm_status.get("rpc_reachable")),
            rpc_url=profile.effective_rpc_url(),
            log_file=str(pm_status.get("log_file") or ""),
            log_tail=list(pm_status.get("last_log_lines") or []),
        )

        sync_payload = self._load_cli_sync_payload(profile.effective_rpc_url())
        if sync_payload:
            summary.rpc_reachable = bool(sync_payload.get("rpc_reachable", summary.rpc_reachable))
            summary.chain_id = _safe_int(sync_payload.get("chain_id"))
            summary.head_number = _safe_int(sync_payload.get("height"))
            summary.head_hash = str(sync_payload.get("head_hash") or "")
            summary.peer_count = _safe_int(sync_payload.get("peer_count"))
            summary.sync = self._build_sync_summary(sync_payload)

        try:
            client = RpcClient(profile.effective_rpc_url(), connect_timeout=3.0, read_timeout=8.0, max_retries=1)
            try:
                if summary.head_number is None:
                    head = client.get_head()
                    summary.head_number = head.number
                    summary.head_hash = head.hash or summary.head_hash
                    summary.rpc_reachable = True
                if summary.chain_id is None:
                    try:
                        summary.chain_id = client.get_chain_id()
                    except Exception:
                        pass
                node_payload = self._load_node_status_payload(client)
                if node_payload:
                    sync_status = node_payload.get("sync") if isinstance(node_payload.get("sync"), dict) else {}
                    p2p_status = node_payload.get("p2p") if isinstance(node_payload.get("p2p"), dict) else {}
                    summary.peer_count = summary.peer_count or _safe_int(p2p_status.get("peers_total"))
                    rpc_sync = self._build_sync_summary(sync_status, fallback=summary.sync)
                    if rpc_sync.detail or rpc_sync.progress_pct is not None or rpc_sync.state != "unknown":
                        summary.sync = rpc_sync
                summary.sync.peer_count = summary.sync.peer_count or summary.peer_count
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001
            summary.last_error = safe_str(exc)

        if summary.sync.peer_count is None:
            summary.sync.peer_count = summary.peer_count
        summary.peer_count = summary.peer_count or summary.sync.peer_count
        return summary

    def _load_node_status_payload(self, client: RpcClient) -> dict[str, Any] | None:
        for method in ("node.getStatus", "node_getStatus"):
            try:
                result = client.call(method, [120])
                if isinstance(result, dict):
                    return result
            except RpcResponseError as exc:
                if exc.rpc_error.code in (-32601, -32602):
                    continue
                raise
            except Exception:
                continue
        return None

    def _build_sync_summary(self, payload: dict[str, Any], fallback: SyncSummary | None = None) -> SyncSummary:
        base = fallback or SyncSummary()
        current_height = _safe_int(
            payload.get("sync_current_height")
            or payload.get("best_block_height")
            or payload.get("currentBlock")
            or payload.get("current_block")
            or payload.get("height")
        )
        target_height = _safe_int(
            payload.get("sync_target_height")
            or payload.get("target_height")
            or payload.get("targetHeight")
            or payload.get("highestBlock")
            or payload.get("best_header_height")
            or payload.get("network_height")
            or payload.get("network_best_height")
        )
        network_height = _safe_int(payload.get("network_height") or payload.get("network_best_height")) or target_height
        progress_pct = payload.get("sync_percent")
        if progress_pct is None and current_height is not None and target_height:
            progress_pct = round(max(0.0, min(100.0, current_height / target_height * 100)), 1)
        phase = str(payload.get("phase") or payload.get("sync_state") or base.phase or "").strip()
        state = phase.upper() if phase else (base.state or "unknown")
        stall_reason = str(payload.get("stall_reason") or base.stall_reason or "")
        peer_count = _safe_int(payload.get("peer_count") or payload.get("peers_total") or payload.get("peerCount"))
        detail_parts: list[str] = []
        if progress_pct is not None:
            detail_parts.append(f"{progress_pct:.1f}% synced")
        if current_height is not None:
            detail_parts.append(f"local {current_height}")
        if network_height is not None:
            detail_parts.append(f"network {network_height}")
        if peer_count is not None:
            detail_parts.append(f"{peer_count} peers")
        detail = " | ".join(detail_parts)
        diagnostics: list[str] = []
        if stall_reason:
            diagnostics.append(f"Stalled: {stall_reason}")
        peer_error = str(payload.get("peer_error") or "").strip()
        if peer_error:
            diagnostics.append(f"Peer issue: {peer_error}")
        if peer_count == 0:
            diagnostics.append("No peers connected yet.")
        return SyncSummary(
            state=state or "unknown",
            phase=phase,
            current_height=current_height,
            target_height=target_height,
            network_height=network_height,
            progress_pct=float(progress_pct) if progress_pct is not None else base.progress_pct,
            peer_count=peer_count,
            stall_reason=stall_reason,
            detail=detail or base.detail,
            diagnostics=diagnostics or list(base.diagnostics),
        )

    def _load_cli_sync_payload(self, rpc_url: str) -> dict[str, Any]:
        try:
            result = run_cli_blocking(
                ["sync", "status", "--json", "--rpc-url", rpc_url],
                timeout_s=20,
                config=self._config,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("sync status CLI failed: %s", exc)
            return {}
        if result.returncode != 0:
            log.debug("sync status CLI returned %s: %s", result.returncode, result.stderr or result.stdout)
            return {}
        return self._extract_json_output(result.stdout or result.stderr or "")

    def _extract_json_output(self, text: str) -> dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    payload = json.loads(raw[start : end + 1])
                    return payload if isinstance(payload, dict) else {}
                except json.JSONDecodeError:
                    return {}
        return {}

    def _collect_mining_summary(self, wallet: WalletSummary) -> FeatureSummary:
        defaults = self._config.mining_defaults if isinstance(self._config.mining_defaults, dict) else {}
        payout = str(defaults.get("miner_address") or wallet.primary_address or "").strip()
        if payout:
            detail = f"Payout address: {shorten_address(payout)}"
            if bool(defaults.get("automine")):
                detail += " | Automine enabled"
            return FeatureSummary("Mining", "ready", detail)
        return FeatureSummary("Mining", "attention", "Choose a payout address before starting mining.")

    def _collect_ena_summary(self) -> FeatureSummary:
        ena = self._config.ena if isinstance(self._config.ena, dict) else {}
        if not bool(ena.get("enabled", True)):
            return FeatureSummary("ENA", "disabled", "ENA is turned off in Settings.")
        provider = str(ena.get("provider") or "local")
        if provider == "remote":
            remote = ena.get("remote") if isinstance(ena.get("remote"), dict) else {}
            endpoint = str(remote.get("endpoint") or "")
            model = str(remote.get("model") or "")
            if endpoint and model:
                return FeatureSummary("ENA", "ready", f"Remote model {model} configured.")
            return FeatureSummary("ENA", "attention", "Remote ENA needs an endpoint and model.")
        return FeatureSummary("ENA", "ready", "Local ENA features are available.")

    def _collect_aicf_summary(self, node: NodeSummary) -> FeatureSummary:
        if not node.rpc_reachable:
            return FeatureSummary("AICF", "attention", "AICF needs a reachable RPC endpoint.")
        return FeatureSummary("AICF", "ready", "Refresh credits and jobs from the AICF page.")

    def _collect_da_summary(self, node: NodeSummary) -> FeatureSummary:
        status = self._da_status.get_status()
        cfg = self._config.da_contribution if isinstance(self._config.da_contribution, dict) else {}
        studio_dir = str(cfg.get("studio_contrib_dir") or cfg.get("studio_dir") or cfg.get("directory") or "")
        usage = self._da_usage.get_snapshot(studio_dir) if studio_dir else None
        if not status.get("enabled"):
            return FeatureSummary("DA", "attention", "DA is not enabled on the current node.", warning=str(status.get("last_error") or ""))
        detail = "DA enabled"
        if usage is not None and studio_dir:
            detail = f"{detail} | {usage.used_bytes / (1024 ** 3):.2f} GiB used in {studio_dir}"
        warning = str(status.get("last_error") or "")
        if usage is not None and usage.warning:
            warning = usage.warning
        return FeatureSummary("DA", "ready" if node.rpc_reachable else "attention", detail, warning=warning)

    def _collect_issues(self, snapshot: StudioSnapshot, wallets: list[WalletRecord]) -> list[StatusIssue]:
        issues: list[StatusIssue] = []
        active_profile = self._active_profile()
        if not wallets:
            issues.append(StatusIssue("warning", "No wallet is set up yet.", "Create or import a wallet to receive funds."))
        if not snapshot.node.rpc_reachable:
            detail = snapshot.node.last_error or snapshot.rpc_url
            if active_profile.type == ProfileType.LOCAL_NODE:
                hint = "Start the local node from the Node page, or switch this profile to External RPC."
                detail = f"{detail} {hint}".strip() if detail else hint
            issues.append(StatusIssue("error", "Studio cannot reach the current RPC endpoint.", detail))
        if snapshot.node.running and (snapshot.node.sync.peer_count or 0) == 0:
            issues.append(StatusIssue("warning", "The node is running but has no peers.", "Use Bootstrap Peers on the Node page."))
        if snapshot.node.sync.stall_reason:
            issues.append(StatusIssue("warning", "Sync looks stalled.", snapshot.node.sync.stall_reason))
        if snapshot.wallet.wallet_count and not snapshot.wallet.total_balance_ok:
            issues.append(StatusIssue("warning", "Wallet balances are unavailable right now.", "Open Wallet to retry or confirm explorer/RPC settings."))
        if snapshot.da.state == "attention":
            issues.append(StatusIssue("warning", "DA needs attention.", snapshot.da.warning or snapshot.da.detail))
        if snapshot.ena.state == "attention":
            issues.append(StatusIssue("info", "ENA needs setup.", snapshot.ena.detail))
        return issues

    def _run_cli_action(self, args: list[str], *, timeout_s: int = 60) -> ServiceActionResult:
        try:
            result = run_cli_blocking(args, timeout_s=timeout_s, config=self._config)
        except Exception as exc:  # noqa: BLE001
            return ServiceActionResult(False, "Command failed to start.", safe_str(exc))
        ok = result.returncode == 0
        summary = "Command completed." if ok else "Command failed."
        details = (result.stdout or result.stderr or "").strip()
        return ServiceActionResult(ok, summary, details, payload={"argv": args, "returncode": result.returncode})
