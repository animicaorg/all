# CLI command guide

This guide collects the main command-line tools shipped in the repository and summarizes how to invoke them plus their key flags.

## Network profiles & orchestration

Profile defaults for devnet/testnet/mainnet live under `ops/profiles/` and
include RPC URLs, Stratum/API binds, DB URIs, and seed lists. Use `ops/run.sh`
to source a profile and start common services:

```
# default devnet (node + pool background, dashboard foreground)
ops/run.sh all

# profile aware shortcuts
ops/run.sh --profile testnet node
ops/run.sh --profile mainnet pool
ops/run.sh dashboard
```

`ops/run.sh all` keeps the node and pool running in the background and exports
`ANIMICA_NETWORK`, `ANIMICA_RPC_URL`, `ANIMICA_STRATUM_BIND`, `ANIMICA_POOL_API_BIND`,
and related DB URIs so the Python CLIs (node, pool, wallet) inherit consistent
settings. Stop the dashboard with `Ctrl+C` to clean up the background services.

## Node CLI

Thin Typer surface for quick JSON-RPC checks. Available as `animica-node` or
`python -m animica.cli.node`:

- `status` prints RPC URL, chain ID, head, sync status, and (when available) the
  full head block.
- `head` dumps the JSON head payload.
- `block --height N` or `block --hash 0x...` fetches a block; height lookups
  fall back to hash queries when needed.
- `tx --hash 0x...` retrieves a transaction by hash.

Example against a running devnet node:

```sh
animica-node status --rpc-url $ANIMICA_RPC_URL
animica-node block --height 1 --rpc-url $ANIMICA_RPC_URL
```

## Pool CLI

Stratum pool helper (same Typer app behind `animica-mining`/`animica-pool`).
Commands:

- `run-pool` starts the Stratum + metrics API using `ANIMICA_RPC_URL`,
  `ANIMICA_STRATUM_BIND`, `ANIMICA_POOL_API_BIND`, and `ANIMICA_MINING_POOL_DB_URL`
  (CLI flags override env vars).
- `show-config` prints the resolved pool configuration.
- `generate-payout-address` mints a dev payout address using the wallet helpers.

Example matching the devnet profile:

```sh
ANIMICA_POOL_PROFILE=hashshare animica-pool run-pool --rpc-url $ANIMICA_RPC_URL
```

## Wallet CLI

Developer-friendly wallet/address helper built on the PQ registry. Invoke via
the console script (`animica-wallet`) or module (`python -m animica.cli.wallet`)
with the following subcommands:

- `create --label <name> [--allow-insecure-fallback]` create a new Dilithium3-
  style keypair, derive a bech32m `anim1…` address, and persist it to
  `~/.animica/wallets.json`.
- `list` show known addresses and algorithms (bech32m/anim HRP).
- `show --address <addr> [--rpc-url ...]` print the wallet entry plus
  `state.getBalance` from the configured RPC endpoint.
- `export --address <addr> --out wallet.json` / `import --file wallet.json`
  round-trip secrets in a JSON format that keeps the bech32m encoding intact.

Example workflow to generate and verify an address against a running node:

```sh
animica-wallet create --label dev1 --allow-insecure-fallback
animica-wallet list

# Query balance over JSON-RPC (state.getBalance)
animica-wallet show --address anim1... --rpc-url $ANIMICA_RPC_URL
```

Addresses emitted by the wallet, explorer, and pool payout configs all follow
`anim` bech32m encoding (alg_id || sha3_256(pubkey)) per `docs/spec/ADDRESSES.md`.

## VM(Py) tooling

### Running commands
All examples below assume the repository root as the working directory. Use the project-managed virtual environment and scripts when available:
- Prefer `pnpm` for Node-based tools (e.g., `pnpm cli <command>` where applicable) and `python -m` for Python entrypoints to ensure dependencies resolve correctly.
- Export any required environment variables (such as `PYTHONPATH` additions) via `source ./scripts/dev/env.sh` before running the commands if your setup depends on repository-local modules.
- If a command is also shipped as a console script (for example, `omni-vm-compile`), you can run it directly or via `python -m` to guarantee the module path is correct.

### `omni-vm-compile`
Compile a deterministic Python contract to Animica VM IR bytes. Works via `python -m vm_py.cli.compile` or the console script alias. Key flags:
- `path/to/contract.py --out out.ir` (required output path) or `--manifest manifest.json --out out.ir`
- `--format {cbor,json}` to pick IR encoding (default CBOR)
- `--meta META.json` to save compile metadata
- `--stdin`/`-` to read source from stdin
- `--quiet` to suppress stderr logs
【F:vm_py/cli/compile.py†L3-L20】【F:vm_py/cli/compile.py†L8-L16】

### `omni-vm-run`
Run a compiled contract for a single function call using a manifest that points to the source/IR. Important arguments:
- `--manifest PATH` (required) to the contract manifest
- `--call NAME` (required) function to invoke
- `--args JSON` to supply a JSON array of call arguments
- `--hex-as-bytes/--no-hex-as-bytes` toggle for converting `0x` strings to bytes
- `--format {text,json}` for the result output (defaults to JSON)
- `--quiet` to silence stderr logging
【F:vm_py/cli/run.py†L3-L17】【F:vm_py/cli/run.py†L296-L315】

### `omni-vm-inspect-ir`
Inspect compiled IR, optionally compiling from a manifest or source first, and report metadata such as gas estimates and hashes. Accepts one of:
- `--ir FILE` to load compiled IR bytes
- `--manifest FILE` to compile a manifest then inspect
- `--source FILE` to compile Python source then inspect
Optional controls: `--format {text,json}`, `--max-depth`, `--max-bytes`, `--show-ir-bytes`, `--quiet`.
【F:vm_py/cli/inspect_ir.py†L3-L48】

## P2P utilities

### `animica-p2p peer`
Peer store maintenance with subcommands:
- `list` show known peers
- `add <peer_id> <addr>` with optional `--probe`/`--timeout`
- `remove <peer_id>`
- `ban <peer_id> --for <duration>`
- `unban <peer_id>`
- `score <peer_id> <score>`
- `export <path>` / `import <path> [--replace]`
- `connect <addr> [--peer-id ...] [--probe --timeout]`
- `disconnect <peer_id>`
- `show <peer_id>` to print JSON details
All commands accept the common `--store` flag from `add_common_store_arg` (default `~/.animica/p2p/peers.json`).
【F:p2p/cli/peer.py†L513-L592】

### `animica-p2p listen`
Start a standalone P2P node wired to the local database. Key flags: `--db` (SQLite URI), `--chain-id`, repeatable `--listen`/`--seed` multiaddrs, `--enable-quic`, `--enable-ws`, `--nat`, and `--log-level`.
【F:p2p/cli/listen.py†L196-L210】

Bootstrap seeds: by default the node uses `ANIMICA_P2P_SEEDS` (comma-separated multiaddrs), and `ops/run.sh` now populates this from `ops/seeds/<profile>.json` while also inserting those seeds into `~/.animica/p2p/peers.json`. Setting `ANIMICA_P2P_SEEDS=""` disables the defaults; providing a list replaces them for both the CLI and the peer store helpers.

### `animica-p2p publish`
Publish a single payload to a gossip topic using a lightweight P2P node. Required topic/payload flags include `--topic` plus one of `--hex`, `--file`, or `--json`. Connectivity flags mirror the listener (`--chain-id`, `--seed`, `--listen`, `--enable-quic`, `--enable-ws`, `--log-level`). Payload handling extras: `--encode {raw,cbor,json}`, `--dry-run`, and `--linger` to wait after publish.
【F:p2p/cli/publish.py†L64-L83】

## Templates engine

### `templates-engine`
Unified interface for working with repository templates:
- `list` enumerates available templates under `templates/`
- `validate --template/-t <path|name> [--print] [--strict]` with optional variable sources via `--vars`, `--var KEY=VAL`, `--vars-json`, or `--env-prefix`
- `render --template/-t <path|name> --out/-o <dir> [--dry-run] [--force] [--exclude GLOB ...] [--print]` plus the same variable-loading options
【F:templates/engine/cli.py†L503-L538】

## SDK code generation

### `sdk.codegen.cli`
Generate contract client stubs from a normalized ABI IR. Invoke as `python -m sdk.codegen.cli` with:
- `--lang {py,ts,rs}` target
- `--abi PATH|-` input ABI JSON
- `--out DIR` destination directory
- Optional `--class` name and `--file` filename
- Advanced overrides for base imports/classes per language (`--py-base-import`, `--ts-base-class`, etc.)
【F:sdk/codegen/cli.py†L388-L402】

## Studio Services admin

### `python -m studio_services.cli`
Administrative Typer app with shared `--config/-c` option. Commands:
- `migrate` apply database migrations/init schema
- `create-api-key` generate/store or print an API key (`--name`, `--scopes`, `--print-only`)
- `list-api-keys` show stored keys (redacted)
- `revoke-api-key <id>` soft-delete a key
- `queue-stats` print verification queue counters
- `backfill` recompute missing artifacts/verifications (`--artifacts/--verifications`, `--dry-run`)
- `gc` garbage-collect orphaned artifacts (`--days`, `--dry-run`)
【F:studio-services/studio_services/cli.py†L92-L146】【F:studio-services/studio_services/cli.py†L183-L338】

## Core sanity helper

### `python -m core.cli_demo`
Lightweight helper to print chain parameters and the current head pointer. Flags: `--db` (database URI), `--genesis` (path to genesis JSON), and `--log` level.
【F:core/cli_demo.py†L4-L64】

## Node pipeline shim

### `python -m aicf.cli.node_pipeline`
Bitcoin-style control surface for the lightweight `aicf.node` RPC shim. Commands share a common `--rpc-url/-r` endpoint flag (defaults to `http://127.0.0.1:8545` and automatically POSTs to `/rpc`), support JSON output via `--json`, and accept `--datadir/-d` to operate directly on a local state directory without RPC calls. Examples:

```sh
python -m aicf.cli.node_pipeline status --json
python -m aicf.cli.node_pipeline mine --count 1 --rpc-url http://127.0.0.1:8545
python -m aicf.cli.node_pipeline block latest --json
python -m aicf.cli.node_pipeline auto true --datadir /tmp/node
python -m aicf.cli.node_pipeline pipeline -m 2 --rpc-url http://127.0.0.1:8545
```

For devnet, the RPC server and core tools share the same SQLite DB and genesis. Once the node is running via `ops/run.sh node`, y
ou can:

```sh
# check status (expects chainId 1337 on devnet)
python -m aicf.cli.node_pipeline status \
  --rpc-url http://127.0.0.1:8545/rpc

# mine 3 blocks via RPC
python -m aicf.cli.node_pipeline mine \
  --count 3 \
  --rpc-url http://127.0.0.1:8545/rpc

# verify the chain state directly from core
python -m core.cli_demo \
  --db "sqlite:////$HOME/animica/devnet/chain.db" \
  --genesis genesis/devnet.json
```

- `status [--json]` prints chain ID, head height, and whether auto-mining is enabled.
- `mine --count/-n <blocks>` bumps the chain height by the requested number of blocks (RPC via miner endpoints or local datadir).
- `block <tag|number> [--json]` fetches a block by number or tag (`latest`, `earliest`, or hex tags) using RPC or local state.
- `auto <true|false>` toggles the miner start/stop RPCs (or flips the local `auto_mine` flag when `--datadir` is used) and prints `on`/`off`.
- `pipeline [--mine/-m <blocks>] [--wait <seconds>] [--json]` runs a scripted workflow of status → mining → head fetch to validate the node surface in one go against either backend.
【F:aicf/cli/node_pipeline.py†L1-L210】
