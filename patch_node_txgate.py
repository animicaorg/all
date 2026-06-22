#!/usr/bin/env python3
"""Patch the mainnet node's tx-submission gate so the authoritative verifier
seed can accept transactions when it's at/above the whole network's height.

The node imports the INSTALLED copy of animica.sync.readiness (in its /data
volume), NOT the repo at /app/python — so this patches that installed file in
place. Idempotent. After running, restart the node:

    docker restart animica-mainnet-node

Why: the seed runs ahead of lagging peers and keeps a few queued look-ahead
blocks, so assess_tx_submission_readiness never sees a "synced" state and
rejects every tx (-32002), which blocks AICF/ANM inference and all submissions.
The added allowance returns True when head_height >= network_best_height (we
hold the leading edge). Nodes genuinely behind are unaffected.
"""
import sys

TARGET = ("/var/lib/docker/volumes/animica_mainnet_chain_1_31ae91ca_data/_data/"
          ".local/lib/python3.11/site-packages/animica/sync/readiness.py")

OLD = '''    if (
        last_header_error == "at_tip"
        and head_height is not None
        and best_header_height is not None
        and head_height >= best_header_height
    ):
        return True, info

    return False, info'''

NEW = '''    if (
        last_header_error == "at_tip"
        and head_height is not None
        and best_header_height is not None
        and head_height >= best_header_height
    ):
        return True, info

    # Authoritative-edge allowance: at/above the highest height any peer reports
    # (network_best) we hold the leading edge; queued look-ahead blocks are our
    # own production, not "behind the network", so allow submission. A
    # block-producing seed ahead of lagging peers otherwise stays "still
    # syncing" forever and can never accept transactions. Behind nodes
    # (head < network_best) are unaffected; an inflated peer height keeps us
    # deferring.
    if (
        head_height is not None
        and network_best_height is not None
        and network_best_height > 0
        and head_height >= network_best_height
        and (best_header_height is None or head_height >= best_header_height)
    ):
        return True, info

    return False, info'''


def main() -> int:
    try:
        src = open(TARGET).read()
    except OSError as e:
        print(f"cannot read {TARGET}: {e}")
        return 2
    if "Authoritative-edge allowance" in src:
        print("already patched — nothing to do")
        return 0
    if OLD not in src:
        print("anchor not found; the installed file differs — patch manually")
        return 3
    open(TARGET, "w").write(src.replace(OLD, NEW, 1))
    # sanity: must still compile
    import py_compile
    py_compile.compile(TARGET, doraise=True)
    print("patched OK — now run: docker restart animica-mainnet-node")
    return 0


if __name__ == "__main__":
    sys.exit(main())
