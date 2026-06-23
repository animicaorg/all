#!/usr/bin/env python3
"""Patch the mainnet node's tx-submission gate (installed copy) so the seed
accepts transactions while it sits at the leading edge of the chain.

The node imports the INSTALLED copy of animica.sync.readiness from its /data
volume, NOT the repo at /app/python — so this patches that installed file in
place. Idempotent. After running, restart the node:

    docker restart animica-mainnet-node

History:
- v1 added an "authoritative-edge" allowance that required network_best_height
  to be known and <= head. But after the proven-peers net_best fix, a node at
  the canonical tip with no proven peer ahead reports network_best = None, so
  that allowance never fired -> the seed rejected every tx with
  -32002 'Node is still syncing' (phase=headers, head==best_header), blocking
  AICF/ANM inference payments.
- v2 (this) BROADENS the allowance: allow when we're caught up on blocks vs our
  own headers (head >= best_header) AND no peer is known ahead (network_best is
  None / <=0 / <= head). Genuinely-behind nodes are unaffected.
"""
import sys

TARGET = ("/var/lib/docker/volumes/animica_mainnet_chain_1_31ae91ca_data/_data/"
          ".local/lib/python3.11/site-packages/animica/sync/readiness.py")

# The v1 (narrow) allowance currently installed.
OLD = '''    if (
        head_height is not None
        and network_best_height is not None
        and network_best_height > 0
        and head_height >= network_best_height
        and (best_header_height is None or head_height >= best_header_height)
    ):
        return True, info'''

# The v2 (broadened) allowance — also covers network_best is None (no proven
# peer ahead = at the canonical tip).
NEW = '''    if (
        head_height is not None
        and (best_header_height is None or head_height >= best_header_height)
        and (
            network_best_height is None
            or network_best_height <= 0
            or head_height >= network_best_height
        )
    ):
        return True, info'''


def main() -> int:
    try:
        src = open(TARGET).read()
    except OSError as e:
        print(f"cannot read {TARGET}: {e}")
        return 2
    if "network_best_height is None" in src:
        print("already broadened (v2) — nothing to do")
        return 0
    if OLD not in src:
        print("v1 anchor not found; installed file differs — patch manually")
        return 3
    open(TARGET, "w").write(src.replace(OLD, NEW, 1))
    import py_compile

    py_compile.compile(TARGET, doraise=True)
    print("patched v2 OK — now run: docker restart animica-mainnet-node")
    return 0


if __name__ == "__main__":
    sys.exit(main())
