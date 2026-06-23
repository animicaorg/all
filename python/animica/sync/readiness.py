from __future__ import annotations

import os
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


def _max_submit_lag_blocks() -> int:
    """Return max tolerated lag for tx submission; -1 disables lag gating."""
    raw = os.environ.get("ANIMICA_TX_SUBMIT_MAX_BEHIND", "5")
    try:
        value = int(str(raw).strip())
    except Exception:
        return 5
    return max(-1, value)


def assess_tx_submission_readiness(
    status: StatusMapping,
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
    max_allowed_behind = _max_submit_lag_blocks()
    blocks_behind = None
    if head_height is not None and best_header_height is not None and best_header_height > head_height:
        blocks_behind = int(best_header_height - head_height)

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
        "blocks_behind": blocks_behind,
        "max_allowed_behind": max_allowed_behind,
    }

    # Primary check: enforce lag ceiling when both heights are known.
    # Default allows near-tip submission (<=5 blocks behind) to avoid false "tx disabled" states.
    if head_height is not None and best_header_height is not None:
        if head_height < best_header_height:
            if max_allowed_behind >= 0 and (best_header_height - head_height) > max_allowed_behind:
                return False, info
            return True, info
        if head_height > best_header_height:
            return True, info
    
    # If we reach here, either heights are equal or heights are unknown.
    # For equal heights or unknown heights, check sync status flags.
    if synchronized is True:
        return True, info
    if syncing_flag is False:
        return True, info
    # IDLE is ambiguous on pip-installed nodes — it can mean "no peers",
    # "behind target but no eligible peers", or "sync disabled", none of
    # which are "at tip". Require explicit synchronized=True or one of the
    # canonical at-tip phases.
    if phase in {"SYNCED", "TARGET_REACHED"}:
        return True, info

    # If heights are at tip and no work in progress, allow
    empty_inflight = (
        pending_header_batches == 0
        and in_flight_headers == 0
        and in_flight_blocks == 0
        and queued_blocks_count == 0
    )

    if head_height is not None and best_header_height is not None:
        if head_height >= best_header_height and empty_inflight:
            return True, info

        header_probe_inflight = (
            pending_header_batches == 0
            and in_flight_blocks == 0
            and queued_blocks_count == 0
            and in_flight_headers <= 1
        )

        if head_height >= best_header_height and header_probe_inflight:
            return True, info

    if (
        last_header_error == "at_tip"
        and head_height is not None
        and best_header_height is not None
        and head_height >= best_header_height
    ):
        return True, info

    # Authoritative-edge allowance: if our head is at or above the highest
    # height ANY peer reports (network_best), we hold the leading edge of the
    # chain we can see. Queued/look-ahead blocks here are our own production,
    # not "behind the network" — so allow submission. Without this, a
    # block-producing verifier seed that runs ahead of lagging peers gets stuck
    # in a permanent "still syncing" state and can never accept transactions.
    # Nodes genuinely behind (head < network_best) are unaffected; a peer
    # claiming an inflated height keeps us deferring (head < that height).
    # network_best may be None when no PROVEN same-chain peer is ahead (e.g. the
    # node is at the canonical tip and its only higher peers are an excluded
    # phantom fork). That is still "at the leading edge", so treat None / <=0 /
    # <=head all as "no peer ahead" and allow, provided we're caught up on blocks
    # vs our own headers. Genuinely-behind nodes (head < best_header, or a proven
    # peer ahead with head < network_best) are unaffected.
    if (
        head_height is not None
        and (best_header_height is None or head_height >= best_header_height)
        and (
            network_best_height is None
            or network_best_height <= 0
            or head_height >= network_best_height
        )
    ):
        return True, info

    return False, info
