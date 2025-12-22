# Animica P2P (Core-style)

Animica's P2P stack now mirrors the reference wire protocol and peer manager behavior while preserving Animica chain rules.

## Ports

* TCP: `30333` (default)

## Message framing

Animica uses the reference-style envelope:

```
magic(4) + command(12) + length(4) + checksum(4) + payload
```

* `magic`: network identifier (default `ANMC`, override with `ANIMICA_P2P_MAGIC`)
* `command`: ASCII, null-padded to 12 bytes
* `length`: little-endian payload length
* `checksum`: first 4 bytes of double-SHA256(payload)

Serialization primitives are reference-compatible: CompactSize varints, little-endian integers, vectored types, and IPv6/IPv4-mapped netaddresses.

## Handshake

Handshake follows reference ordering:

1. `version` exchanged on connect
2. `verack` exchanged once `version` is validated
3. Peers that send non-handshake messages before `verack` are disconnected

The `version` payload includes protocol version, service flags, timestamps, `addr_recv`/`addr_from`, nonce, user agent, start height, and relay flag.

## Peer discovery

* `addr` and `getaddr` are supported
* Nodes maintain an in-memory addr manager (last_seen/last_success/failures/score)
* On handshake, nodes request addresses and relay a small sample (10–50)
* Every ~30–60s connected peers receive a randomized address sample excluding already-announced entries
* Learned peers are persisted to `p2p/peers.json` (plus `peers.db` when available)
* RFC1918/loopback addresses are filtered unless `ANIMICA_P2P_PRIVATE_NETWORK=true`

### External address announcement

* Prefer explicit config: `ANIMICA_P2P_ADVERTISE_ADDR` or `ANIMICA_P2P_EXTERNAL_IP`
* Optional auto-detect: set `ANIMICA_P2P_EXTERNAL_IP_ENDPOINT` to a public IP service URL
* If no external address is known, the node advertises nothing (but still accepts inbound)

## Inventory relay

Animica uses reference-style inventory relay:

* `inv` to announce tx/block hashes
* `getdata` to request missing items
* `notfound` to respond to missing inventory

Per-peer known-inventory filters prevent redundant relays.

## Headers-first sync

`getheaders`/`headers` drives headers-first sync. Blocks are requested via `getdata` once headers are accepted.

## Migration notes

* Legacy HELLO/IDENTIFY and gossip frames remain available in the old P2P stack.
* New core-style stack lives in `p2p/core_p2p/` and is integrated by the node service.

## Observability

Structured logs include peer connect/disconnect, handshake completion, addr acceptance, inv relay, and headers progress.
