"""Container healthcheck: GET /healthz on the local RPC port."""
import os
import sys
import urllib.request

port = os.environ.get("ANIMICA_RPC_PORT", "8545")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=4) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
