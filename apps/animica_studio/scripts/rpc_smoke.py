#!/usr/bin/env python3
"""RPC smoke test: call discover + get_head on the configured rpc_url.

Run from apps/animica_studio:
    python scripts/rpc_smoke.py [rpc_url]
"""

from __future__ import annotations

import sys
import os

# Allow running from apps/animica_studio without installing
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from animica_studio.storage.config import load_config
from animica_studio.services.rpc_client import RpcClient, RpcTransportError, RpcResponseError


def main() -> int:
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        cfg = load_config()
        url = cfg.get_active_profile().rpc_url

    print(f"RPC smoke test → {url}")
    client = RpcClient(url, connect_timeout=5.0, read_timeout=10.0, max_retries=2)

    # --- discover ---
    print("\n[1] rpc.discover …")
    try:
        disc = client.discover()
        methods = disc.get("methods", [])
        print(f"    OK — {len(methods)} methods")
        for m in methods[:10]:
            name = m.get("name", m) if isinstance(m, dict) else m
            print(f"      • {name}")
        if len(methods) > 10:
            print(f"      … and {len(methods) - 10} more")
    except RpcTransportError as exc:
        print(f"    TRANSPORT ERROR: {exc}")
    except RpcResponseError as exc:
        print(f"    RPC ERROR: {exc}")
    except Exception as exc:
        print(f"    ERROR: {exc}")

    # --- get_head ---
    print("\n[2] chain_getHead …")
    try:
        head = client.get_head()
        print(f"    OK — block #{head.number}  hash={head.hash[:20]}…  ts={head.timestamp}")
    except RpcTransportError as exc:
        print(f"    TRANSPORT ERROR: {exc}")
    except RpcResponseError as exc:
        print(f"    RPC ERROR: {exc}")
    except Exception as exc:
        print(f"    ERROR: {exc}")

    client.close()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
