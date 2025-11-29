# CLI command guide

This guide collects the main command-line tools shipped in the repository and summarizes how to invoke them plus their key flags.

## VM(Py) tooling

### `omni-vm-compile`
Compile a deterministic Python contract to Animica VM IR bytes. Works via `python -m vm_py.cli.compile` or the console script alias. Key flags:
- `path/to/contract.py --out out.ir` (required output path)
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
