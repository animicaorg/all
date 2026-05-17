"""Provider abstraction for `animica chat`.

Each provider implements ``is_available`` and ``serve``. The runtime walks
``integration.yaml::agent_runtime.provider_order`` and uses the first
available provider. Failures cascade unless
``ANIMICA_CHAT_REQUIRE_DISTRIBUTED=1`` is set.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping, Optional

from agent_runtime.aicf_client import AICFClient, JobChunk, JobSpec
from agent_runtime.config import Config
from agent_runtime.errors import (
    AICFError,
    AgentRuntimeError,
    BundleError,
    ProviderUnavailable,
    WalletError,
)
from agent_runtime.wallet import (
    WalletInfo,
    get_next_nonce,
    load_wallet_info,
    preview_payment,
    sign_payment,
)


# --------------------------------------------------------------------------- #
# Types                                                                       #
# --------------------------------------------------------------------------- #

@dataclass
class TurnResult:
    """A single completed assistant turn."""

    text: str
    provider: str
    tier: str = ""
    requested_tier: Optional[str] = None
    effective_mode: str = ""
    cost_animica: float = 0.0
    latency_ms: int = 0
    fallback_reasons: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class TurnRequest:
    prompt: str
    tier_preferred: Optional[str] = None
    history: list[dict[str, str]] = field(default_factory=list)
    max_output_tokens: int = 1024
    temperature: float = 0.2
    top_p: float = 0.95
    require_provider: Optional[str] = None    # forces a single provider
    stream_callback: Optional["StreamFn"] = None
    yolo: bool = False                         # override balance refusal


StreamFn = "callable"      # type: ignore[assignment] — informational alias


# --------------------------------------------------------------------------- #
# Provider base                                                               #
# --------------------------------------------------------------------------- #

class Provider(ABC):
    name: str = "provider"

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """Return (ok, reason). reason is informational when ok is True."""

    @abstractmethod
    def serve(self, req: TurnRequest) -> TurnResult:
        ...

    def close(self) -> None:    # pragma: no cover — default is no-op
        pass


# --------------------------------------------------------------------------- #
# distributed-aicf                                                            #
# --------------------------------------------------------------------------- #

class DistributedAICFProvider(Provider):
    name = "distributed-aicf"

    def __init__(self, *, cfg: Config, rpc_url: str,
                 wallet_path: Optional[str] = None,
                 wallet_label: Optional[str] = None) -> None:
        self.cfg = cfg
        self.rpc_url = rpc_url
        self.wallet_path = wallet_path
        self.wallet_label = wallet_label
        self.client = AICFClient(
            endpoint=rpc_url,
            timeout_sec=float(cfg.integration["aicf"]["job_submit"][
                "timeout_sec"]),
            poll_interval_ms=int(cfg.integration["aicf"]["job_submit"][
                "poll_interval_ms"]),
            max_retries=int(cfg.integration["aicf"]["job_submit"][
                "max_retries"]),
            retry_backoff_ms=list(cfg.integration["aicf"]["job_submit"][
                "retry_backoff_ms"]),
        )
        self._wallet: Optional[WalletInfo] = None

    def _wallet_info(self) -> WalletInfo:
        if self._wallet is None:
            self._wallet = load_wallet_info(
                wallet_path=self.wallet_path,
                rpc_url=self.rpc_url,
                network=os.environ.get("ANIMICA_NETWORK", ""),
                wallet_label=self.wallet_label,
            )
        return self._wallet

    def is_available(self) -> tuple[bool, str]:
        try:
            wi = self._wallet_info()
        except WalletError as exc:
            return False, f"wallet_unavailable: {exc.message}"
        # Distinguish "balance lookup failed" (RPC down, wrong method, etc.)
        # from "balance is genuinely zero" — both used to report as
        # wallet_balance_zero, which masked node connectivity problems.
        if not wi.balance_lookup_ok:
            return False, (
                f"balance_lookup_failed: {wi.balance_lookup_error or 'unknown'}"
            )
        if wi.balance_animica <= 0.0:
            return False, "wallet_balance_zero"
        if not self.client.ping():
            return False, f"aicf_endpoint_unreachable: {self.rpc_url}"
        return True, "ok"

    def serve(self, req: TurnRequest) -> TurnResult:
        t0 = time.time()
        wi = self._wallet_info()
        spec = JobSpec(
            prompt=req.prompt,
            tier_preferred=req.tier_preferred or
                str(self.cfg.model_catalog["routing"]["default_tier"]),
            max_output_tokens=req.max_output_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            metadata={"history_len": len(req.history)},
        )
        quote = self.client.estimate_cost(spec)
        preview = preview_payment(wi, quote.estimated_cost_animica)
        if not preview.sufficient and not req.yolo:
            raise ProviderUnavailable(
                self.name,
                preview.reason or "insufficient_balance",
            )
        nonce = get_next_nonce(self.rpc_url, wi.address)
        # Treasury address — prefer the on-chain canonical (aicf.getTreasuryAddress
        # served by the local node). Fall back to the integration config, and
        # finally to a placeholder that the server-side payment decoder will
        # reject if ANIMICA_AICF_REQUIRE_SETTLEMENT=1. The fetched address is
        # also a valid bech32, which the placeholder "aicf-treasury" was not.
        recipient = ""
        try:
            tres = self.client._rpc("aicf.getTreasuryAddress", {})
            if isinstance(tres, dict):
                recipient = str(tres.get("treasury_address") or "")
        except Exception:
            recipient = ""
        if not recipient:
            recipient = str(self.cfg.integration["aicf"].get(
                "treasury_address", "aicf-treasury"
            ))
        signed = sign_payment(
            wi,
            amount_animica=quote.estimated_cost_animica,
            recipient=str(recipient),
            chain_id=wi.chain_id,
            nonce=nonce,
            rpc_url=self.rpc_url,
            job_metadata={"job_kind": spec.job_kind,
                           "tier": spec.tier_preferred},
        )
        sub = self.client.submit(spec, signed_payment=signed.__dict__)
        accumulated: list[str] = []
        cb = req.stream_callback

        def relay(ch: JobChunk) -> None:
            accumulated.append(ch.text)
            if cb is not None:
                cb(ch.text, ch.is_final)

        for _ in self.client.stream(sub.job_id, on_chunk=relay):
            pass
        settle = self.client.settle(sub.job_id)
        text = settle.text or "".join(accumulated)
        return TurnResult(
            text=text,
            provider=self.name,
            tier=sub.accepted_tier or spec.tier_preferred or "",
            requested_tier=spec.tier_preferred,
            effective_mode="real",
            cost_animica=settle.actual_cost_animica,
            latency_ms=settle.latency_ms or int((time.time() - t0) * 1000),
            metadata={"job_id": sub.job_id,
                      "provider_id": settle.provider_id},
        )

    def close(self) -> None:
        self.client.close()


# --------------------------------------------------------------------------- #
# local-flagship                                                              #
# --------------------------------------------------------------------------- #

class LocalFlagshipProvider(Provider):
    name = "local-flagship"

    def __init__(self, *, cfg: Config, bundle_root: Optional[Path] = None
                 ) -> None:
        self.cfg = cfg
        root_cfg = cfg.integration["agent_runtime"]["providers"][
            "local-flagship"]
        self.bundle_root = bundle_root or (
            cfg.repo_root / root_cfg["bundle_root"]
        )
        self.bundle_pointer = root_cfg["bundle_pointer"]
        self._loaded_inference: Optional[dict] = None
        self._bundle_dir: Optional[Path] = None

    def _resolve_bundle(self) -> Optional[Path]:
        ptr = self.bundle_root / self.bundle_pointer
        if ptr.is_symlink() or ptr.is_dir():
            return ptr.resolve() if ptr.is_symlink() else ptr
        return None

    def is_available(self) -> tuple[bool, str]:
        bundle = self._resolve_bundle()
        if bundle is None or not bundle.is_dir():
            return False, f"no_bundle_at:{self.bundle_root}/{self.bundle_pointer}"
        manifest = bundle / "manifest.json"
        if not manifest.is_file():
            return False, "bundle_missing_manifest"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"bundle_manifest_invalid: {exc}"
        if not bool(data.get("available_for_real_inference", False)):
            return False, "bundle_not_marked_real_inference"
        inference = bundle / "inference.json"
        if not inference.is_file():
            return False, "bundle_missing_inference_json"
        try:
            self._loaded_inference = json.loads(
                inference.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"inference_json_invalid: {exc}"
        self._bundle_dir = bundle
        return True, "ok"

    def serve(self, req: TurnRequest) -> TurnResult:
        ok, reason = self.is_available()
        if not ok:
            raise BundleError(f"local-flagship not usable: {reason}")
        assert self._bundle_dir is not None
        assert self._loaded_inference is not None

        # The local bundle is loaded lazily via flagship_agent.inference.
        # When transformers/torch aren't installed we degrade to a clear
        # error rather than hallucinating output.
        try:
            from flagship_agent.inference import LocalBundleRunner
        except Exception as exc:
            raise BundleError(
                f"flagship_agent.inference unavailable: {exc}",
                hint="install ai/flagship_agent (or the animica wheel + [ai] "
                     "extra) to use local-flagship",
            ) from exc
        t0 = time.time()
        runner = LocalBundleRunner(
            bundle_dir=self._bundle_dir,
            inference_spec=self._loaded_inference,
        )
        chunks: list[str] = []

        def relay(text: str, is_final: bool) -> None:
            chunks.append(text)
            if req.stream_callback is not None:
                req.stream_callback(text, is_final)

        runner.generate(
            prompt=req.prompt,
            history=req.history,
            max_output_tokens=req.max_output_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            on_chunk=relay,
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        manifest = json.loads(
            (self._bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        return TurnResult(
            text="".join(chunks),
            provider=self.name,
            tier=str(manifest.get("tier", "")),
            requested_tier=req.tier_preferred,
            effective_mode=str(manifest.get("effective_mode", "")),
            cost_animica=0.0,
            latency_ms=elapsed_ms,
            metadata={"bundle_dir": str(self._bundle_dir),
                       "run_id": str(manifest.get("run_id", ""))},
        )


# --------------------------------------------------------------------------- #
# offline                                                                     #
# --------------------------------------------------------------------------- #

class OfflineProvider(Provider):
    name = "offline"

    def __init__(self, *, cfg: Config) -> None:
        self.cfg = cfg
        self._templates = {
            "no_wallet": (
                "I can't reach the AICF network and don't have a local "
                "model bundle yet. Configure a wallet with `animica wallet "
                "new` and top it up, or build a local fallback bundle:\n\n"
                "    bash ai/flagship_agent/scripts/train_flagship_agent.sh"
            ),
            "no_bundle": (
                "There's no local flagship bundle on this machine. Either "
                "fund a wallet and let `animica chat` use the distributed "
                "provider, or run:\n\n"
                "    bash ai/flagship_agent/scripts/train_flagship_agent.sh"
            ),
            "generic": (
                "I'm running in offline mode without a model. I can only "
                "echo a short static reply. The chat REPL printed the "
                "reason earlier; check the /provider line."
            ),
        }

    def is_available(self) -> tuple[bool, str]:
        return True, "ok"

    def serve(self, req: TurnRequest) -> TurnResult:
        reasons = " ".join(req.history[-1].get("fallback_reasons", "")
                            if req.history else [])
        if "wallet_unavailable" in reasons:
            text = self._templates["no_wallet"]
        elif "no_bundle" in reasons:
            text = self._templates["no_bundle"]
        else:
            text = self._templates["generic"]
        if req.stream_callback is not None:
            req.stream_callback(text, True)
        return TurnResult(
            text=text,
            provider=self.name,
            tier="",
            requested_tier=req.tier_preferred,
            effective_mode="offline",
            cost_animica=0.0,
            latency_ms=0,
        )


# --------------------------------------------------------------------------- #
# Cascade                                                                     #
# --------------------------------------------------------------------------- #

class ProviderCascade:
    """Owns provider instances; walks through them per the configured order."""

    def __init__(self, cfg: Config, *, rpc_url: str,
                 wallet_path: Optional[str] = None,
                 wallet_label: Optional[str] = None) -> None:
        self.cfg = cfg
        order = list(cfg.integration["agent_runtime"]["provider_order"])
        self._providers: list[Provider] = []
        for name in order:
            if name == "distributed-aicf":
                self._providers.append(DistributedAICFProvider(
                    cfg=cfg, rpc_url=rpc_url, wallet_path=wallet_path,
                    wallet_label=wallet_label))
            elif name == "local-flagship":
                self._providers.append(LocalFlagshipProvider(cfg=cfg))
            elif name == "offline":
                self._providers.append(OfflineProvider(cfg=cfg))
            else:
                raise AgentRuntimeError(
                    f"unknown provider in integration.yaml provider_order: "
                    f"{name!r}",
                )

    def close(self) -> None:
        for p in self._providers:
            try:
                p.close()
            except Exception:    # pragma: no cover — best-effort
                pass

    def serve(self, req: TurnRequest) -> TurnResult:
        require_distributed = os.environ.get(
            "ANIMICA_CHAT_REQUIRE_DISTRIBUTED") == "1"
        reasons: list[str] = []
        forced = req.require_provider
        for p in self._providers:
            if forced and forced != p.name:
                continue
            ok, reason = p.is_available()
            if not ok:
                reasons.append(f"{p.name}: {reason}")
                if require_distributed and p.name == "distributed-aicf":
                    raise ProviderUnavailable(
                        "distributed-aicf",
                        f"{reason}; ANIMICA_CHAT_REQUIRE_DISTRIBUTED=1",
                    )
                continue
            try:
                result = p.serve(req)
            except (AICFError, BundleError, WalletError,
                    ProviderUnavailable) as exc:
                reasons.append(f"{p.name}: serve_failed: {exc.message}")
                if require_distributed and p.name == "distributed-aicf":
                    raise
                continue
            result.fallback_reasons = list(reasons)
            return result
        raise AgentRuntimeError(
            "no provider available",
            detail={"tried": reasons},
        )

    def provider_status(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for p in self._providers:
            ok, reason = p.is_available()
            out.append({"name": p.name,
                        "available": "yes" if ok else "no",
                        "reason": reason})
        return out
