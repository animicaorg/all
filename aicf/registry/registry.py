from __future__ import annotations

import inspect
from dataclasses import is_dataclass, replace
from typing import Any, List, Mapping, Optional

from aicf.errors import RegistryError
from aicf.registry import verify_attest
from aicf.registry.provider import Capability, Provider, ProviderStatus


class Allowlist:
    def __init__(
        self, denied_ids: set[str] | None = None, denied_regions: set[str] | None = None
    ) -> None:
        self._ids = set(denied_ids or ())
        self._regions = set(denied_regions or ())

    def is_denied(self, provider_id: str, region: str) -> bool:
        return provider_id in self._ids or region in self._regions


class Registry:
    def __init__(self, allowlist: Optional[Allowlist] = None) -> None:
        self._allow = allowlist or Allowlist()
        self._providers: dict[str, Provider] = {}

    def _build_provider(
        self,
        provider_id: str,
        capabilities: Capability,
        endpoints: Mapping[str, str],
        stake: int,
        region: str,
    ) -> Provider:
        sig = None
        try:
            sig = inspect.signature(Provider)  # type: ignore[arg-type]
        except Exception:
            pass

        kw: dict[str, Any] = {}
        # id / provider_id
        if sig and "id" in sig.parameters:
            kw["id"] = provider_id
        elif sig and "provider_id" in sig.parameters:
            kw["provider_id"] = provider_id
        else:
            kw["id"] = provider_id

        # capabilities / caps
        if not sig or "capabilities" in sig.parameters:
            kw["capabilities"] = capabilities
        elif "caps" in sig.parameters:
            kw["caps"] = capabilities

        # status
        if not sig or "status" in sig.parameters:
            kw["status"] = ProviderStatus.ACTIVE

        # endpoints
        if not sig or "endpoints" in sig.parameters:
            kw["endpoints"] = dict(endpoints)

        # extras if present
        for name, value in (
            ("stake", stake),
            ("region", region),
            ("jailed", False),
            ("jail_until_height", 0),
            ("violations", 0),
            ("meta", {}),
        ):
            if sig and name in sig.parameters:
                kw[name] = value

        # Fill remaining required params with None for harness variants
        if sig:
            for pname, p in sig.parameters.items():
                if pname == "self":
                    continue
                if p.default is inspect._empty and pname not in kw:
                    kw[pname] = None

        return Provider(**kw)  # type: ignore[call-arg]

    def register_provider(
        self,
        provider_id: str,
        capabilities: Capability,
        endpoints: Mapping[str, str],
        attestation: bytes,
        stake: int,
        region: str,
    ) -> Provider:
        if self._allow.is_denied(provider_id, region):
            raise RegistryError("provider denied by allowlist")
        if not verify_attest.verify_attestation(attestation):
            raise RegistryError("attestation failed")

        prov = self._build_provider(provider_id, capabilities, endpoints, stake, region)
        self._providers[provider_id] = prov
        return prov

    def get_provider(self, provider_id: str) -> Optional[Provider]:
        return self._providers.get(provider_id)

    def list_providers(self) -> List[Provider]:
        return list(self._providers.values())

    def update_capabilities(self, provider_id: str, new_caps: Capability) -> Provider:
        p = self._providers[provider_id]
        if is_dataclass(p):
            p2 = replace(p, capabilities=new_caps)
        else:
            setattr(p, "capabilities", new_caps)
            p2 = p
        self._providers[provider_id] = p2
        return p2

    def update_endpoints(
        self, provider_id: str, new_eps: Mapping[str, str]
    ) -> Provider:
        p = self._providers[provider_id]
        if is_dataclass(p):
            p2 = replace(p, endpoints=dict(new_eps))
        else:
            setattr(p, "endpoints", dict(new_eps))
            p2 = p
        self._providers[provider_id] = p2
        return p2

    def set_status(self, provider_id: str, status: ProviderStatus) -> Provider:
        p = self._providers[provider_id]
        if is_dataclass(p):
            p2 = replace(p, status=status)
        else:
            setattr(p, "status", status)
            p2 = p
        self._providers[provider_id] = p2
        return p2
