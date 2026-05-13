# animica-node

The **Animica full-node operator CLI**, distributed via npm. It's a
production control plane that provisions and supervises a managed Animica
node runtime. On a fresh machine, the workflow is:

```sh
npm install -g animica-node
animica-node init
animica-node start
```

That's it — no repo clone, no `pip install`, no `.venv` to manage.
`animica-node start` will auto-provision the managed runtime under
`~/.animica/runtime/` on first use.

## Quickstart

```sh
animica-node init            # writes ~/.animica/node/node.json
animica-node start           # auto-installs runtime if missing; runs detached
animica-node status          # pid, RPC, sync, config snapshot
animica-node doctor          # backend + RPC + miner safety checks
animica-node logs -f         # tail node logs
animica-node stop
```

## How the runtime gets installed

`animica-node` ships only the npm control plane. The actual node binary
lives on the release channel:

1. `animica-node install-runtime` (auto-invoked by `start`) fetches
   `https://releases.animica.org/runtime/stable/manifest.json`.
2. For your platform (`linux-x64`, `darwin-arm64`, `win32-x64`, …) it
   downloads the matching tarball.
3. The download is SHA-256-verified against the manifest, extracted
   atomically, and pinned under `~/.animica/runtime/versions/`.
4. A `current.json` pointer activates the install. Future `start`
   invocations re-use it.

Override the manifest URL with `ANIMICA_RUNTIME_MANIFEST_URL` (handy for
private channels, mirrored deployments, or air-gapped operators).

To bring your own binary instead — e.g. operators who build from source —
set `ANIMICA_NODE_BIN=/path/to/animica`. That override is honored before
any other resolution.

## Backend resolution order

1. `$ANIMICA_NODE_BIN` (operator override)
2. Managed runtime under `~/.animica/runtime/` (production default)
3. Legacy: `animica` on PATH (existing dev workstations)
4. Legacy: `<repoRoot>/.venv/bin/python -m animica.cli.main`
5. Legacy: `python3 -m animica.cli.main` (last resort)

Paths 3–5 exist so existing development setups keep working. They are no
longer the primary path for new users.

## Commands

### Core

| Command | Description |
| --- | --- |
| `init` | Generate / refresh `node.json` |
| `start [-f\|--foreground] [--no-install]` | Start the node (detached by default; auto-installs the runtime unless `--no-install`) |
| `stop` | Stop the running node |
| `restart` | Stop + start |
| `status` | Pidfile, RPC, sync state, config snapshot |
| `logs [-f]` | Tail node logs |
| `doctor` | RPC, backend, miner-safety checks |
| `reset --yes` | Wipe the configured data dir |

### Runtime management

| Command | Description |
| --- | --- |
| `install-runtime [--manifest-url URL] [--force]` | Install (or re-install) the managed runtime |
| `runtime status` | Where the runtime lives, which version is active |
| `runtime repair` | Verify every install has its marker + entry |
| `runtime remove --all \| --channel C --version V` | Delete one or all installed runtimes |
| `runtime prune [--keep N]` | Drop old installs, keep most recent N + active |
| `runtime doctor` | Manifest reachability + backend resolution |

### Chain / config

| Command | Description |
| --- | --- |
| `rpc call <method> [params…]` | JSON-RPC pass-through |
| `sync status` | One-liner sync state |
| `peers` | List connected peers |
| `config show` / `config set k=v` | Read / mutate `node.json` |
| `wallet path` | Print the wallet dir used by the node |
| `miner status` | Miner-aware hints for resourceMode |
| `balance [addr]` | Suggested agent flow for balance lookup |
| `discovery` | JSON used by `animica-agent` to align RPC |

## Configuration

`~/.animica/node/node.json` (override via `ANIMICA_NODE_CONFIG`):

```json
{
  "network": "local-devnet",
  "chainId": 1,
  "rpcPort": 8545,
  "p2pPort": 30303,
  "metricsPort": 9106,
  "dataDir": "~/.animica/node/data",
  "logLevel": "info",
  "minerSafeMode": false,
  "resourceMode": "balanced"
}
```

`resourceMode=miner-priority` propagates to the node runtime as
`ANIMICA_NODE_RESOURCE_MODE=miner-priority` for operators running a hot
miner alongside the node.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `ANIMICA_NODE_BIN` | Override the resolved backend binary (highest priority) |
| `ANIMICA_NODE_HOME` | Override the daemon state dir (default `~/.animica/node`) |
| `ANIMICA_NODE_CONFIG` | Override the config file path |
| `ANIMICA_RUNTIME_HOME` | Override the managed-runtime install root (default `~/.animica/runtime`) |
| `ANIMICA_RUNTIME_MANIFEST_URL` | Override the manifest URL `install-runtime` fetches |

## Release engineering

Release engineers (not end users) can produce the artifacts the manifest
points at using the scripts under `scripts/`:

```sh
# Build a runtime tarball for the current host.
node scripts/build-runtime-bundle.mjs \
  --version 0.2.0 \
  --src    /path/to/animica/python/source \
  --python /path/to/relocatable/python \
  --out    dist/runtime-bundles

# Generate the manifest after building tarballs for every target platform.
node scripts/generate-runtime-manifest.mjs \
  --dir     dist/runtime-bundles \
  --base    https://releases.animica.org/runtime/stable \
  --version 0.2.0 \
  --out     dist/runtime-bundles/manifest.json

# Upload everything under dist/runtime-bundles/ to the release host.
```

Both scripts are pure Node.js and produce identical output across hosts.
The test suite (`test/bundle-roundtrip.test.ts`) installs a freshly built
bundle into a temp dir to prove the full pipeline before publishing.

## Integration with `animica-agent`

`animica-agent` automatically discovers this node's config and uses
`http://127.0.0.1:<rpcPort>/rpc` for `doctor`, `status`, `balance`,
`rpc call`, and useful-work submissions. When the managed runtime is
active, the agent also dispatches wallet operations through it — no
separate `pip install animica` is needed.

## License

Apache-2.0.
