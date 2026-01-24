# P2P Smoke Harness

This harness boots two local P2P nodes, waits for handshake completion, keeps the
connection alive for 30 seconds, simulates mining a block on node A, and checks
that node B observes the tip height update.

## Run

```bash
python tools/p2p_smoke/run_smoke.py
```

Expected output includes:

```
✅ smoke passed: handshake stable, tip propagated to height 1
```

If you need to change ports or add logging, edit `run_smoke.py`.
