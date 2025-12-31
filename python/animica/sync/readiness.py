from __future__ import annotations

import typing as t


StatusMapping = t.Mapping[str, t.Any]


def _coerce_int(value: t.Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _first_int(status: StatusMapping, keys: t.Iterable[str]) -> int | None:
    for key in keys:
        if key in status:
            value = _coerce_int(status.get(key))
            if value is not None:
                return value
    return None


def assess_tx_submission_readiness(
    status: StatusMapping,
    *,
    tip_tolerance: int = 2,
) -> tuple[bool, dict[str, t.Any]]:
    phase_raw = status.get("phase") or status.get("state")
    phase = str(phase_raw).upper() if phase_raw is not None else ""
    synchronized = status.get("synchronized")
    syncing_flag = status.get("syncing")

    head_height = _first_int(
        status,
        (
            "head_height",
            "headHeight",
            "height",
            "blockHeight",
            "currentBlock",
            "current_block",
            "best_block_height",
            "bestBlockHeight",
        ),
    )
    best_header_height = _first_int(
        status,
        (
            "best_header_height",
            "bestHeaderHeight",
            "best_header",
            "highestBlock",
            "target_height",
            "targetHeight",
        ),
    )
    best_block_height = _first_int(
        status,
        (
            "best_block_height",
            "bestBlockHeight",
            "best_block",
            "head_height",
        ),
    )
    network_best_height = _first_int(
        status,
        (
            "network_best_height",
            "networkBestHeight",
        ),
    )
    pending_header_batches = _first_int(
        status,
        ("pending_header_batches", "pendingHeaderBatches"),
    )
    in_flight_headers = _first_int(
        status,
        ("in_flight_headers", "inFlightHeaders", "in_flight"),
    )
    in_flight_blocks = _first_int(
        status,
        ("in_flight_blocks", "inFlightBlocks"),
    )
    queued_blocks_count = _first_int(
        status,
        ("queued_blocks_count", "queuedBlocksCount"),
    )
    last_header_error = status.get("last_header_error") or status.get("lastHeaderError")

    in_flight_headers = in_flight_headers or 0
    in_flight_blocks = in_flight_blocks or 0
    queued_blocks_count = queued_blocks_count or 0
    pending_header_batches = pending_header_batches or 0

    info = {
        "phase": phase or None,
        "synchronized": synchronized,
        "syncing": syncing_flag,
        "head_height": head_height,
        "best_header_height": best_header_height,
        "best_block_height": best_block_height,
        "network_best_height": network_best_height,
        "pending_header_batches": pending_header_batches,
        "in_flight_headers": in_flight_headers,
        "in_flight_blocks": in_flight_blocks,
        "queued_blocks_count": queued_blocks_count,
        "last_header_error": last_header_error,
    }

    if synchronized is True:
        return True, info
    if syncing_flag is False:
        return True, info
    if phase in {"SYNCED", "IDLE", "TARGET_REACHED"}:
        return True, info

    empty_inflight = (
        pending_header_batches == 0
        and in_flight_headers == 0
        and in_flight_blocks == 0
        and queued_blocks_count == 0
    )

    if head_height is not None and best_header_height is not None:
        delta = best_header_height - head_height
        if delta <= 0 and (best_block_height is None or best_block_height >= best_header_height):
            if empty_inflight:
                return True, info
        if delta <= tip_tolerance and empty_inflight:
            return True, info

    if (
        last_header_error == "at_tip"
        and head_height is not None
        and best_header_height is not None
        and head_height >= best_header_height
    ):
        return True, info

    return False, info
