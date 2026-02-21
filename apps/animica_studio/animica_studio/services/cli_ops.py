"""Operation-based CLI command builders backed by CliRegistry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from animica_studio.services.cli_registry import CliRegistry


class CliOperation(str, Enum):
    WALLET_CREATE = "wallet_create"
    WALLET_LIST = "wallet_list"
    AICF_STATUS = "aicf_status"
    AICF_JOBS_WATCH = "aicf_jobs_watch"
    MINE_BLOCKS = "mine_blocks"


@dataclass
class OperationSpec:
    path_group: str
    display_name: str
    required_opts: tuple[str, ...] = ()


_SPECS: dict[CliOperation, OperationSpec] = {
    CliOperation.WALLET_CREATE: OperationSpec("wallet_create", display_name="wallet create", required_opts=("--label", "--alg")),
    CliOperation.WALLET_LIST: OperationSpec("wallet_list", display_name="wallet list"),
    CliOperation.AICF_STATUS: OperationSpec("aicf_status", display_name="aicf status"),
    CliOperation.AICF_JOBS_WATCH: OperationSpec("aicf_jobs_watch", display_name="aicf jobs watch"),
    CliOperation.MINE_BLOCKS: OperationSpec("mine_blocks", display_name="mine-blocks"),
}


class CliOperationError(RuntimeError):
    pass


class CliOps:
    def __init__(self, registry: CliRegistry) -> None:
        self._registry = registry

    def selected_path(self, op: CliOperation) -> list[str]:
        spec = _SPECS[op]
        display_name = spec.display_name
        path = self._registry.best_match(spec.path_group)
        if not path:
            raise CliOperationError(
                f"Your animica CLI does not support {display_name}. "
                f"Detected commands: {', '.join(self._registry.top_level_commands()) or '<none>'}."
            )
        for req in spec.required_opts:
            if not self._registry.has_opt(path, req):
                raise CliOperationError(
                    f"Your animica CLI does not support {display_name}: missing required option {req} "
                    f"for {' '.join(path)}."
                )
        return path

    def build(self, op: CliOperation, params: dict[str, Any] | None = None) -> list[str]:
        params = params or {}
        path = self.selected_path(op)

        if op is CliOperation.WALLET_CREATE:
            label = str(params["label"])
            alg = str(params["alg"])
            out = [*path, "--label", label, "--alg", alg]
            if params.get("allow_insecure_fallback") and self._registry.has_opt(path, "--allow-insecure-fallback"):
                out.append("--allow-insecure-fallback")
            return out

        if op is CliOperation.WALLET_LIST:
            return path

        if op is CliOperation.AICF_STATUS:
            return path

        if op is CliOperation.AICF_JOBS_WATCH:
            job_id = str(params["job_id"])
            return [*path, job_id]

        if op is CliOperation.MINE_BLOCKS:
            count = int(params.get("count", 1))
            out = [*path, "--count", str(count)]

            address = str(params.get("address") or "").strip()
            if address:
                if self._registry.has_opt(path, "--address"):
                    out.extend(["--address", address])
                elif self._registry.has_opt(path, "--miner"):
                    out.extend(["--miner", address])
                else:
                    raise CliOperationError(
                        "Your animica CLI does not expose a payout address option for mine-blocks."
                    )
            return out

        raise CliOperationError(f"Unsupported operation: {op.value}")
