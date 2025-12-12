# Animica Monorepo

Animica is a layer-1 blockchain focused on verifiable AI and quantum-secure execution. This repository houses the node, consensus, execution engine, wallets, SDKs, explorer, studio, documentation, and supporting tooling for running and extending the network.

## What lives in this repo

- **Core protocol:** `core/`, `consensus/`, `execution/`, `mempool/`, `rpc/`, `p2p/`, `mining/`
- **Cryptography & proofs:** `proofs/`, `zk/`, `pq/`, `randomness/`
- **Wallets & explorer:** `wallet/` (Flutter), `wallet-extension/` (browser), `explorer-web/`
- **Studio & tooling:** `studio-web/`, `studio-wasm/`, `studio-services/`, `templates/`
- **SDKs & APIs:** `sdk/` (Python/TypeScript/Rust), `docs/` (specs and guides), `spec/` (canonical schemas)
- **Ops & installers:** `ops/`, `tests/devnet/`, `installers/`, `chains/` (network metadata)

Use the module READMEs inside each folder for deeper details.

## Prerequisites

- Python **3.11+** with `venv` and `pip`
- Node.js **20+** with npm
- Docker and Docker Compose (for devnet)
- Build tools such as `build-essential`, `pkg-config`, and `libssl-dev` if you plan to build Rust components

## Setup

```bash
# Clone the repository
git clone https://github.com/animicaorg/all.git
cd all

# Install base toolchain and dependencies
./setup.sh

# Activate Python environment
source .venv/bin/activate
```

## Working with networks

Animica ships with multiple network profiles:

- **mainnet** (chain ID 1) – production network and default for commands
- **testnet** (chain ID 2) – public testing network
- **devnet** (chain ID 1337) – local development network
- **local-devnet** – alternative local profile with distinct ports

The CLI automatically scopes data directories and default RPC endpoints per network. Switch contexts via an environment variable or flag:

```bash
export ANIMICA_NETWORK=devnet
animica node status
# or per command
animica --network devnet node status
```

## Run a local devnet

The quickest way to bring up a full stack (node, miner, explorer, and services) is Docker Compose:

```bash
export ANIMICA_NETWORK=devnet
bash tests/devnet/up.sh
# Check running containers
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet ps
```

Node logs are available via `docker compose ... logs -f node1`, and RPC defaults to `http://localhost:38545` for the local profile.

## Testing

```bash
# Run everything (Python + JS + Rust suites)
./testall.sh

# Python-only entry point
pytest -q

# Targeted suites
pytest consensus/tests/ -v
pytest execution/tests/ -v
pytest rpc/tests/ -v
pytest mempool/tests/ -v
pytest p2p/tests/ -v

# Fast smoke subset
pytest -m "not slow and not integration" -q
```

## Documentation

- **Authoritative docs:** see `docs/` for specs, guides, and architecture overviews.
- **Quickstarts:** `QUICKSTART.md` (network setup), `docs/dev/CONTRACTS_START.md` (VM(Py) contracts), `wallet/README.md` (Flutter wallet), and `rpc/rpc-quickstart.md` (JSON-RPC usage).
- **Schemas:** `spec/openrpc.json` and `spec/abi.schema.json` describe RPC and ABI surfaces.

## Contributing

- Keep changes scoped to a single area when possible and follow the style and testing guidelines in local READMEs.
- Include runnable commands in docs and ensure new examples pin versions where applicable.
- Squash-and-merge with a clear summary once reviews pass.

## Support

- File issues or proposals with purpose, audience, scope, and acceptance criteria.
- For security-sensitive topics (keys, proofs, VKs, installer signing), request a security review before merging.
