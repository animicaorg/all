## Peer accounting and RPC robustness updates

- **Unified peer view:** The P2P service now exposes a deduplicated peer registry shared with RPC methods. Inbound sessions are capped per IP, unknown handshakes time out, and duplicate peer IDs are pruned in favor of the newest connection.
- **New RPCs:** `net.peerCount` and `net.peers` expose the authoritative peer snapshot for CLI/monitoring tools while returning structured JSON-RPC errors when the peer subsystem is unavailable.
- **CLI improvements:** `animica sync status` uses the new RPC peer count and reports “unavailable” instead of `0` when peer data cannot be fetched. `animica node status` now fails fast after bounded retries with exponential backoff instead of retrying indefinitely.
