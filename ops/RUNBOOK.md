# Ops Runbook — node lifecycle quick actions

This guide documents the exact commands to start, restart, inspect, and recover Animica nodes. Commands assume you run them from the repo root unless stated otherwise.

## Start a new node
- Devnet (docker compose):
  ```bash
docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet up -d node
  ```
- Local script with profiles (auto-loads env from `ops/profiles/<name>.env`):
  ```bash
./ops/run.sh --profile devnet node
  ```

## Host P2P vs Docker nodes (port conflicts)
- `./setup.sh` now defaults to **not** starting a host P2P listener when docker compose files are present.
  - Opt in: `./setup.sh --p2p` or `ENABLE_P2P=1 ./setup.sh`
  - Opt out: `./setup.sh --no-p2p` or `DISABLE_P2P=1 ./setup.sh`
- Docker nodes use host ports `8545` (RPC) and `30333` (P2P TCP) by default.
- Host P2P (outside docker) defaults to port `30334` to avoid collisions unless you override it.
- If you previously started host P2P on `30333`, stop it or run:
  ```bash
animica node up --kill-conflicts
  ```
  This stops the Animica-owned PID in `logs/animica-p2p.pid` before docker starts.

## Restart a node
- Docker compose:
  ```bash
docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet restart node
  ```
- Kubernetes:
  ```bash
kubectl rollout restart statefulset/animica-node -n animica-devnet
kubectl rollout status statefulset/animica-node -n animica-devnet --timeout=5m
  ```

## Check logs
- Docker compose (follow tail):
  ```bash
docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet logs -f --tail=200 node
  ```
- Kubernetes pod (statefulset replica 0):
  ```bash
kubectl logs statefulset/animica-node -n animica-devnet --tail=200 -f
  ```
- Systemd (if running as a service):
  ```bash
sudo journalctl -u animica-node.service -f
  ```

## Recover from a stalled chain (head not advancing)
1. Confirm the stall (height stays constant across two samples):
   ```bash
RPC_URL=${RPC_URL:-http://localhost:8545/rpc}
curl -sX POST "$RPC_URL" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'
sleep 10
curl -sX POST "$RPC_URL" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'
   ```
2. Bounce node + miner to clear stuck state:
   ```bash
# docker compose
docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet restart node miner
# kubernetes
kubectl rollout restart statefulset/animica-node -n animica-devnet
kubectl rollout restart deploy/animica-miner -n animica-devnet
   ```
3. Verify recovery (head advancing + explorer/metrics):
   ```bash
./ops/scripts/wait_for.sh http http://localhost:8545/healthz 120
./ops/scripts/smoke_devnet.sh --strict
   ```
4. If height is still frozen, clear local devnet DB **(destructive; dev use only)** and re-run step 1:
   ```bash
rm -rf ~/animica/devnet/chain.db
./ops/run.sh --profile devnet node
   ```

## Rotate logs
- Docker json-file logs on the host (safe copytruncate rotation):
  ```bash
sudo tee /tmp/animica-logrotate.conf >/dev/null <<'CONF'
/var/lib/docker/containers/*/*.log {
  rotate 5
  daily
  compress
  copytruncate
  missingok
  notifempty
}
CONF
sudo logrotate -f /tmp/animica-logrotate.conf
  ```
- Systemd journals (if node runs as a service):
  ```bash
sudo journalctl --rotate
sudo journalctl --vacuum-time=3d
  ```
