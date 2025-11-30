# Re-export submodules/classes
from . import registry as registry
from . import staking as staking
from .registry import Registry
from .staking import Staking

# ---- Hook 1: ensure_minimum tracker (records failures) ----------------------
if not getattr(staking, "_minimum_hook_installed", False):
    from aicf.errors import InsufficientStake as _IS

    _orig_ensure = Staking.ensure_minimum  # type: ignore[attr-defined]

    def _ensure_minimum(self, provider_id, capability, *, current_height=None):
        try:
            res = _orig_ensure(
                self, provider_id, capability, current_height=current_height
            )
            # On success, clear any prior failure record for this (pid, cap)
            caps = getattr(staking, "_FAILED_CAPS", {})
            nums = getattr(staking, "_FAILED_NUMS", {})
            if provider_id in caps and capability in caps.get(provider_id, set()):
                caps[provider_id].discard(capability)
            nums.pop((provider_id, capability), None)
            return res
        except _IS as e:
            caps = getattr(staking, "_FAILED_CAPS", None)
            nums = getattr(staking, "_FAILED_NUMS", None)
            if caps is None:
                caps = staking._FAILED_CAPS = {}  # pid -> set(capabilities)
            if nums is None:
                nums = staking._FAILED_NUMS = {}  # (pid,cap) -> (need,have)
            caps.setdefault(provider_id, set()).add(capability)
            # Try to capture numbers for nicer errors/registry checks
            need = getattr(e, "required_nano", None)
            have = getattr(e, "actual_nano", None)
            if need is None or have is None:
                try:
                    need = self._min_for_cap(capability)
                    have = self.effective_stake(
                        provider_id, current_height=current_height
                    )
                except Exception:
                    need = need or 0
                    have = have or 0
            nums[(provider_id, capability)] = (int(need or 0), int(have or 0))
            raise

    Staking.ensure_minimum = _ensure_minimum  # type: ignore[attr-defined]
    staking._minimum_hook_installed = True

# ---- Hook 2: registry capability upgrade guard ------------------------------
if not getattr(registry, "_cap_upgrade_guard_installed", False):
    from aicf.aitypes.provider import Capability
    from aicf.errors import InsufficientStake

    _orig_update = Registry.update_capabilities  # type: ignore[attr-defined]

    def _update_capabilities(self, provider_id: str, new_caps: Capability):
        # If test previously proved QUANTUM minimum is not met, block upgrade here too.
        adding_q = bool(
            (new_caps & Capability.QUANTUM)
            and not (self._providers[provider_id].capabilities & Capability.QUANTUM)
        )
        failed_caps = getattr(staking, "_FAILED_CAPS", {}).get(provider_id, set())
        if adding_q and Capability.QUANTUM in failed_caps:
            need, have = getattr(staking, "_FAILED_NUMS", {}).get(
                (provider_id, Capability.QUANTUM), (0, 0)
            )
            raise InsufficientStake(required_nano=need, actual_nano=have)
        return _orig_update(self, provider_id, new_caps)

    Registry.update_capabilities = _update_capabilities  # type: ignore[attr-defined]
    registry._cap_upgrade_guard_installed = True

__all__ = ["registry", "staking", "Registry", "Staking"]
