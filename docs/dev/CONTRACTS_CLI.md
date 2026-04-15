# Animica Contract CLI

The `animica contract` command group provides an end-to-end terminal workflow for Python-VM contract lifecycle:

- compile source to artifact
- deploy artifact/source
- call read-only methods
- send state-changing methods
- inspect deployment metadata and saved aliases

## Commands

```bash
animica contract compile <source>
animica contract deploy <artifact-or-source>
animica contract call <contract> <method>
animica contract send <contract> <method>
animica contract inspect <artifact-or-deployment>
animica contract address <name>
animica contract estimate-gas <contract> <method>
animica contract encode-calldata <method>
animica contract decode-result <method>
animica contract list-artifacts
```

Run `animica contract --help` for full options.

## Local Storage Layout

Contract CLI state is stored under `~/.animica/contracts/` (or `$ANIMICA_HOME/contracts`):

- `artifacts/` compiled artifacts and artifact index
- `deployments/<network-key>/` deployment records by network/chain scope

Saved deployment records include:

- `name`, `address`, `chain_id`, `network`
- `tx_hash`, `block_height`, `deployer`
- `abi_path`, `artifact_path`, `manifest_path`
- `code_hash`, `rpc_url`, `created_at`
- constructor metadata when provided

## Counter Happy Path

The repository includes a Counter sample at `vm_py/examples/counter/contract.py`.

```bash
# 1) Compile
animica contract compile vm_py/examples/counter/contract.py \
  --out ./build/counter.avm \
  --abi-out ./build/counter.abi.json \
  --manifest-out ./build/counter.manifest.json \
  --overwrite

# 2) Deploy and save alias
animica contract deploy ./build/counter.avm \
  --from main \
  --abi ./build/counter.abi.json \
  --save \
  --name counter \
  --wait

# 3) Read method
animica contract call counter get

# 4) State-changing method
animica contract send counter inc --from main --wait

# 5) Read again
animica contract call counter get
```

## JSON Args Examples

All method/constructor args are accepted as JSON arrays or named objects:

```bash
animica contract call counter set --args '[5]'
animica contract send counter set --from main --args '{"n": 6}'
animica contract deploy ./build/counter.avm --from main --abi ./build/counter.abi.json --constructor-args '[1]'
```

## Alias Resolution

When a deployment is saved (`--save --name <alias>`), later commands can use the alias:

```bash
animica contract address counter
animica contract inspect counter
animica contract call counter get
animica contract send counter inc --from main
```

To resolve directly by address, pass the contract address instead of alias.

## JSON Output

Use `--json` on deploy/call/send/address/inspect/list commands for stable machine-readable payloads.

Example:

```bash
animica contract deploy ./build/counter.avm --from main --abi ./build/counter.abi.json --wait --json
```

## Troubleshooting

- RPC unavailable:
  - pass `--rpc <url>` explicitly, or set `ANIMICA_RPC_URL`
  - verify node health with `animica node status`
- Wallet label not found:
  - inspect wallets with `animica wallet list`
  - pass `--from <label_or_address>` or `--label <label>`
- Invalid ABI:
  - ensure ABI JSON is valid and method names/types match contract ABI
  - pass `--abi <path>` or deploy with `--save` so ABI path is persisted
- Contract alias not found:
  - inspect saved deployments with `animica contract list-artifacts`
  - use `animica contract address <alias>` to verify resolution
- Transaction pending:
  - run with `--wait` to block for receipt
  - query status via `animica rpc call tx.getStatus '["<tx_hash>"]'`
- Gas estimation failed:
  - use `animica contract estimate-gas <contract> <method>`
  - override with `--gas-limit` on `contract send` or `contract deploy`
