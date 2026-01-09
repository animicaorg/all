# Snapshots

Animica snapshots provide fast, resumable bootstrap for new nodes and automated recovery
for nodes that fall behind. Snapshots are created from fully-synced nodes, published
as chunked artifacts plus a manifest, and applied automatically when safe.

## Automatic behavior

Snapshots are enabled by default on mainnet/testnet. On startup, if the local DB is
missing, empty, or genesis-only, the node will:

1. Fetch the latest trusted snapshot manifest for the current network.
2. Download snapshot chunks (HTTP range resume supported).
3. Verify chunk hashes and (optionally) manifest signatures.
4. Apply the snapshot with an atomic DB swap.
5. Resume P2P sync from the snapshot head.

During sync, a watchdog monitors progress. If the node stalls with peers connected,
the watchdog escalates recovery actions and can automatically apply a newer snapshot
when configured and safe.

When snapshots are applied during recovery, the node uses P2P discovery first, then
falls back to manifest URLs. Snapshot recovery is rate-limited with a cooldown and
per-window cap to avoid infinite retry loops.

## Manifest format (summary)

Manifest JSON includes:

- `schema_version`
- `chain_id` and optional `network`
- `created_at` (UTC)
- `head_height`, `head_hash`
- `chunks[]` with `name`, `size`, `sha256`
- Optional signature metadata

Each snapshot directory also maintains an `inventory.json` file that lists available
snapshots (height/hash, created_at, manifest hash, sizes). Snapshot listing and P2P
advertisement read from this inventory when present.

## Configuration

Environment variables:

- `ANIMICA_SNAPSHOT_AUTO` (default on for mainnet/testnet)
- `ANIMICA_SNAPSHOT_MANIFEST_URL_MAINNET`
- `ANIMICA_SNAPSHOT_MANIFEST_URL_TESTNET`
- `ANIMICA_SNAPSHOT_MANIFEST_URLS` (comma-separated override)
- `ANIMICA_SNAPSHOT_MANIFEST_URL_FALLBACKS` (comma-separated)
- `ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS` (comma-separated hex pubkeys)
- `ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE` (fail if signature missing/invalid)
- `ANIMICA_SNAPSHOT_COOLDOWN_SECS`
- `ANIMICA_SNAPSHOT_MIN_ADVANCE_BLOCKS`
- `ANIMICA_SNAPSHOT_RECOVERY_WINDOW_SECS`
- `ANIMICA_SNAPSHOT_RECOVERY_MAX_PER_WINDOW`

If your node runs in a container and stores snapshots under `/data`, you can map
paths for CLI output by setting:

- `ANIMICA_SNAPSHOT_HOST_DIR` (host-visible snapshots root)

## Publishing snapshots (HTTP + P2P)

Snapshots are written to the local snapshots directory (typically
`~/.animica/snapshots/chain-<id>-height-<height>/`). To publish them over HTTP:

1. Copy the snapshot directory contents to a web server directory (e.g. `/data/snapshots`).
2. Serve the directory with nginx or any static file server.
3. Point nodes at the manifest URL(s) using `ANIMICA_SNAPSHOT_MANIFEST_URLS`.

Nodes also advertise snapshots over P2P for discovery and download. If a manifest
URL is configured, the node prefers HTTP manifests first and falls back to P2P
snapshot discovery when manifests are unavailable.

## CLI

Snapshots can be managed manually with:

- `animica snapshot create`
- `animica snapshot verify`
- `animica snapshot download`
- `animica snapshot apply`
- `animica snapshot bootstrap`

Manual commands are optional; the node will auto-bootstrap by default when safe.
