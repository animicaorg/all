// SN51 (Celium/Lium) executor provisioning script, served per-enrollment.
// Mirrors the Datura compute-subnet executor install (verified 2026-06-04):
// sysbox runtime is MANDATORY for full rewards; Docker >= 29.2 breaks
// sysbox+GPU (CDI conflict); the miner↔executor trust link is just
// MINER_HOTKEY_SS58_ADDRESS in the executor's .env.
export interface ProvisionOpts {
  minerHotkey: string;
  externalPort: number;
  sshPort: number;
  rentingPortRange: string;
  executorRef: string; // our BittensorExecutor id, echoed for support
  paused: boolean;
}

export function provisionScript(o: ProvisionOpts): string {
  return `#!/usr/bin/env bash
# Animica Pool × Bittensor SN51 (Celium/Lium) — executor provisioning
# Executor ref: ${o.executorRef}
${o.paused ? "# NOTE: Animica's SN51 miner is not yet registered on-chain — this rig\n# will install cleanly and start earning the moment the pool flips live.\n" : ""}set -euo pipefail

MINER_HOTKEY="${o.minerHotkey}"
EXTERNAL_PORT=${o.externalPort}
SSH_PORT=${o.sshPort}
RENTING_PORT_RANGE="${o.rentingPortRange}"

echo "==> Preflight"
command -v nvidia-smi >/dev/null || { echo "ERROR: nvidia-smi missing (install NVIDIA driver)"; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker missing"; exit 1; }
KERNEL_MAJOR=$(uname -r | cut -d. -f1); KERNEL_MINOR=$(uname -r | cut -d. -f2)
if [ "$KERNEL_MAJOR" -lt 6 ] || { [ "$KERNEL_MAJOR" -eq 6 ] && [ "$KERNEL_MINOR" -lt 5 ]; }; then
  echo "ERROR: kernel >= 6.5 required for the GPU+sysbox path (have $(uname -r))"; exit 1
fi
DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 0)
case "$DOCKER_VER" in
  29.[2-9]*|3[0-9]*) echo "ERROR: Docker $DOCKER_VER breaks sysbox+GPU (CDI conflict) — pin Docker < 29.2"; exit 1 ;;
esac

echo "==> Fetch SN51 compute-subnet"
cd /opt
if [ ! -d compute-subnet ]; then
  git clone https://github.com/Datura-ai/compute-subnet.git
fi
cd compute-subnet

echo "==> Executor host setup (docker deps)"
chmod +x scripts/install_executor_on_ubuntu.sh
./scripts/install_executor_on_ubuntu.sh

echo "==> Sysbox + NVIDIA runtime (mandatory — validators reject rigs without sysbox-runc)"
cd neurons/executor
chmod +x nvidia_docker_sysbox_setup.sh
./nvidia_docker_sysbox_setup.sh

echo "==> Configure executor .env"
cat > .env <<EOF
MINER_HOTKEY_SS58_ADDRESS=$MINER_HOTKEY
INTERNAL_PORT=8080
EXTERNAL_PORT=$EXTERNAL_PORT
SSH_PORT=$SSH_PORT
RENTING_PORT_RANGE=$RENTING_PORT_RANGE
EOF

echo "==> Start executor"
docker compose up -d

echo "==> Verify GPU attestation path"
docker run --rm --runtime=sysbox-runc --gpus all daturaai/compute-subnet-executor:latest nvidia-smi

echo "==> Done. Keep this rig ONLINE: SN51 burns collateral if a rented GPU"
echo "    drops mid-rental, and your held-back earnings stand behind it."
echo "    Open ports: $EXTERNAL_PORT (validator), $SSH_PORT (ssh), $RENTING_PORT_RANGE (rentals)."
`;
}
