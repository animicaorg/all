"""Shared status models for Studio home, onboarding, and diagnostics views."""

from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass
class StatusIssue:
    level: str
    title: str
    detail: str = ""


@dataclass
class WalletEntrySummary:
    label: str
    address: str
    sig_scheme: str = ""
    balance_text: str = "Unavailable"
    balance_ok: bool = False
    balance_reason: str = ""


@dataclass
class WalletSummary:
    wallet_count: int = 0
    selected_address: str | None = None
    selected_label: str = ""
    selected_balance_text: str = "Unavailable"
    selected_balance_reason: str = ""
    primary_address: str = ""
    total_balance_text: str = "—"
    total_balance_ok: bool = False
    explorer_ready: bool = False
    recent_txs: list[dict[str, str]] = field(default_factory=list)
    wallets: list[WalletEntrySummary] = field(default_factory=list)


@dataclass
class SyncSummary:
    state: str = "unknown"
    phase: str = ""
    current_height: int | None = None
    target_height: int | None = None
    network_height: int | None = None
    progress_pct: float | None = None
    peer_count: int | None = None
    stall_reason: str = ""
    detail: str = ""
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class NodeSummary:
    running: bool = False
    rpc_reachable: bool = False
    rpc_url: str = ""
    chain_id: int | None = None
    head_number: int | None = None
    head_hash: str = ""
    peer_count: int | None = None
    sync: SyncSummary = field(default_factory=SyncSummary)
    last_error: str = ""
    log_file: str = ""
    log_tail: list[str] = field(default_factory=list)


@dataclass
class FeatureSummary:
    title: str
    state: str
    detail: str
    warning: str = ""


@dataclass
class OnboardingProbe:
    has_profile: bool = False
    has_wallet: bool = False
    rpc_reachable: bool = False
    node_running: bool = False
    sync_complete: bool = False
    selected_network: str = "mainnet"
    wallet_count: int = 0
    issues: list[StatusIssue] = field(default_factory=list)


@dataclass
class StudioSnapshot:
    generated_at: float = field(default_factory=time.time)
    profile_name: str = ""
    network_name: str = ""
    rpc_url: str = ""
    wallet: WalletSummary = field(default_factory=WalletSummary)
    node: NodeSummary = field(default_factory=NodeSummary)
    mining: FeatureSummary = field(default_factory=lambda: FeatureSummary("Mining", "unknown", "Not checked yet."))
    ena: FeatureSummary = field(default_factory=lambda: FeatureSummary("ENA", "unknown", "Not checked yet."))
    aicf: FeatureSummary = field(default_factory=lambda: FeatureSummary("AICF", "unknown", "Not checked yet."))
    da: FeatureSummary = field(default_factory=lambda: FeatureSummary("DA", "unknown", "Not checked yet."))
    issues: list[StatusIssue] = field(default_factory=list)
