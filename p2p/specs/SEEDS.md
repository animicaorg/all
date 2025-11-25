# Animica Seed Lists (v1)

This document specifies how nodes discover initial peers before the gossip/sync layer is online. It covers **formats** (DNS and HTTPS JSON), **validation**, **rotation**, and **abuse-handling**. It corresponds to:

- Implementation: `p2p/discovery/seeds.py`
- Config: `p2p/config.py` (per-network seed endpoints)
- Transport/address parsing: `p2p/transport/multiaddr.py`
- Peer identity: `p2p/crypto/peer_id.py` (sha3-256(pubkey||alg_id))

Networks use CAIP-2 chain IDs (e.g., `animica:1` mainnet, `animica:2` testnet, `animica:1337` devnet).

---

## 1) Sources & precedence

Nodes combine multiple seed sources and deduplicate:

1. **HTTPS JSON seed list** (signed; preferred)
2. **DNS TXT/A/AAAA seed records** (DNSSEC-signed where available)
3. **Embedded fallbacks** (static list compiled with release; very small)

Precedence rule: prefer HTTPS JSON if signature and freshness checks pass. Merge DNS entries that are fresh. Embedded entries are used only when both 1 and 2 fail.

---

## 2) HTTPS JSON Seed List

### 2.1 Endpoint
Per network, publish at:

- Mainnet: `https://seeds.animica.org/v1/animica:1.json`
- Testnet: `https://seeds.animica.org/v1/animica:2.json`
- Devnet:  `https://seeds.animica.org/v1/animica:1337.json`

Each JSON has a detached signature at the same path with `.sig` suffix (see **2.4 Signature**).

### 2.2 Canonical JSON schema (stable)
```jsonc
{
  "version": 1,
  "network": "animica:1",        // CAIP-2
  "generatedAt": "2025-01-20T12:34:56Z",
  "expiresAt":   "2025-02-03T12:34:56Z",   // MUST be <= 14 days after generatedAt
  "entries": [
    {
      "peerId": "anim1pe...xz",          // OPTIONAL: if known; sha3-256(pub||alg_id) bech32m
      "addrs": [
        "/ip4/203.0.113.10/tcp/37001",
        "/ip6/2001:db8::1/udp/37001/quic",
        "/dns/seed1.animica.org/tcp/37001/ws"
      ],
      "features": ["full","tx","blocks","shares"], // tags
      "asn": 64496,                      // OPTIONAL (for diversity)
      "weight": 10,                      // 1..100 default 10; initial dial priority
      "note": "EU-West, full archive"    // OPTIONAL human hint
    }
  ],
  "contact": "ops@animica.org",          // OPTIONAL
  "metadata": { "source": "curated+telemetry", "minVersion": ">=1.0.0" }
}

Canonicalization: The JSON MUST be encoded in canonical form (UTF-8, sorted object keys, no insignificant whitespace). We reuse the project’s canonical JSON rules (core/utils/serialization.py) so the detached signature is stable across implementations.

2.3 Client validation

A client MUST verify:
	•	version == 1
	•	network matches local chain
	•	generatedAt <= now <= expiresAt and (expiresAt - generatedAt) <= 14 days
	•	Detached signature (2.4) is valid for the canonical bytes
	•	Each addrs[] decodes as a valid multiaddr supported by local transports
	•	Diversity: reject lists where >60% entries share the same ASN or /16 (/32 for IPv6 /32 equivalent) unless --allow-low-diversity is set.

2.4 Signature (detached; Ed25519)
	•	File: <path>.json.sig contains Base64 of the Ed25519 signature over the canonical JSON bytes.
	•	Key distribution:
	•	A primary long-term seedlist signing key (ed25519-public-key as base32 or hex) is published inside the repo (p2p/discovery/seeds.py: TRUSTED_KEYS) and may be updated only via normal releases.
	•	Optional key-rollover: the signed JSON may include metadata.nextKey and the file is signed by both old and new keys during the overlap period; clients accept either.

CLI verification example (minisign-format optional) is out-of-scope; implementations verify in-process using libsodium/ed25519.

⸻

3) DNS Seeds (TXT + A/AAAA)

3.1 Zones
	•	Mainnet: _seed.animica.org
	•	Testnet: _seed.testnet.animica.org
	•	Devnet:  _seed.devnet.animica.org

3.2 Record formats

TXT (multiaddr rows, comma-separated key=val):

seedX._seed.animica.org.  300 IN TXT "ma=/ip4/203.0.113.21/tcp/37001,feat=full,asn=64496,w=10"
seedY._seed.animica.org.  300 IN TXT "ma=/ip6/2001:db8::42/udp/37001/quic,feat=full,asn=64497,w=10"
seedZ._seed.animica.org.  300 IN TXT "ma=/dns/seed3.animica.org/tcp/37001/ws,feat=tx,asn=64498,w=5"

A / AAAA (legacy fallback):

seed4._seed.animica.org. 300 IN A    203.0.113.44
seed4._seed.animica.org. 300 IN AAAA 2001:db8::44
; Default port 37001/tcp unless TXT overrides with ma=…

Optional SRV is permitted:

_p2p._tcp._seed.animica.org. 300 IN SRV 10 60 37001 seed4._seed.animica.org.

3.3 DNSSEC
	•	Zones SHOULD be signed with DNSSEC.
	•	Clients that can validate DNSSEC SHOULD prefer DNSSEC-validated answers.
	•	If DNSSEC is absent, clients lower weight of DNS-seeded peers by 25%.

3.4 Parsing rules
	•	Each TXT value is parsed as key=value pairs:
	•	ma (REQUIRED): multiaddr
	•	feat (OPTIONAL): full|tx|blocks|shares (single or +-joined)
	•	asn (OPTIONAL): integer
	•	w (OPTIONAL): weight 1..100 (default 10)
	•	Multiple TXT records under one label are allowed; treat each as a separate entry.
	•	TTL SHOULD be 300s; clients cache up to TTL, but not beyond 1 hour.

⸻

4) Bootstrapping flow
	1.	Load embedded fallback (very small: 3–5 entries, diverse regions).
	2.	Fetch HTTPS JSON; verify signature & freshness; merge.
	3.	Query DNS TXT/A/AAAA; prefer DNSSEC-validated; merge.
	4.	Shuffle with weighted randomization; enforce diversity:
	•	No more than 1 peer per ASN in the first 4 dials.
	•	No more than 2 peers per /16 (IPv4) or /32 (IPv6) in the first 8 dials.
	5.	Dial in parallel with exponential backoff (respect p2p/peer/ratelimit.py).
	6.	Persist address book entries with observed success/failure (see p2p/peer/address_book.py), decaying weights over time.

⸻

5) Rotation & freshness
	•	Seed lists must be regenerated at least weekly, with expiresAt no farther than 14 days out.
	•	Auto-prune entries with:
	•	50% connection failure over last week
	•	Persistent protocol/version mismatches
	•	Misbehavior (e.g., malformed frames, spam); see Abuse below
	•	Operators SHOULD include at least 5 ASNs, 3 regions, and both IPv4 and IPv6.

Client refresh policy:
	•	Refresh HTTPS JSON every 24h ± jitter (20%).
	•	Refresh DNS every TTL or 15m, whichever is longer.
	•	On startup, always attempt a fresh HTTPS fetch (non-blocking) while dialing with cached data.

⸻

6) Abuse handling & delisting

Reasons to delist:
	•	Serving incorrect chain (wrong chainId)
	•	Systematic DoS (oversized frames, invalid encryption, protocol violations)
	•	Malicious gossip (fabricated headers/blocks/shares)
	•	Excessive connection churn or SYN flood

Policy:
	•	Record concrete evidence (packet captures, logs with message ids/timestamps).
	•	Quarantine an entry for 48h (weight=0) before removal unless egregious.
	•	Publish removals in the next signed HTTPS list.
	•	Provide a public appeal channel (contact field).

Client-side:
	•	Maintain a local denylist (hash of addrs[] or peerId); never dial denied entries even if reappearing via DNS.
	•	Share aggregated abuse signals (counts, reasons) when opting into telemetry (off by default).

⸻

7) Examples

7.1 Example HTTPS JSON (canonical)

{
  "entries": [
    {
      "addrs": [
        "/ip4/203.0.113.10/tcp/37001",
        "/ip6:2001:db8::10/udp/37001/quic"
      ],
      "asn": 64496,
      "features": ["full","blocks","shares"],
      "note": "EU-West",
      "peerId": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0k2u5",
      "weight": 12
    },
    {
      "addrs": [
        "/dns/seed-na.animica.org/tcp/37001/ws"
      ],
      "asn": 64497,
      "features": ["full","tx"],
      "weight": 10
    }
  ],
  "expiresAt": "2025-02-03T12:34:56Z",
  "generatedAt": "2025-01-20T12:34:56Z",
  "metadata": { "minVersion": ">=1.0.0", "source": "curated" },
  "network": "animica:1",
  "version": 1
}

Detached signature: Base64 in animica:1.json.sig.

7.2 Example DNS zone (TXT + A/AAAA)

$ORIGIN _seed.animica.org.
seed1   300 IN TXT "ma=/ip4/203.0.113.21/tcp/37001,feat=full,asn=64496,w=10"
seed2   300 IN TXT "ma=/ip6/2001:db8::22/udp/37001/quic,feat=full,asn=64497,w=10"
seed3   300 IN TXT "ma=/dns/seed3.animica.org/tcp/37001/ws,feat=tx,asn=64498,w=5"
seed4   300 IN A    203.0.113.44
seed4   300 IN AAAA 2001:db8::44
_p2p._tcp 300 IN SRV 10 60 37001 seed4


⸻

8) Security notes
	•	Prefer HTTPS with HSTS; pin to the seedlist signing key rather than TLS certs (TLS protects transport, signature protects content).
	•	DNSSEC verification is a strong plus but not required on all clients.
	•	Do not embed private addresses (RFC1918, ULA) in public networks; clients MUST drop them unless --allow-private-peers is set for test/dev.
	•	Enforce address sanitation in p2p/discovery/seeds.py: normalize multiaddrs, strip unsupported transports, drop non-routable IPs.

⸻

9) Implementation checklist
	•	Canonical JSON parser/serializer for signature verification
	•	Ed25519 detached signature verify with trusted keys
	•	DNS TXT/A/AAAA/SRV parser with optional DNSSEC
	•	Weighted, diversity-aware shuffle
	•	Persistent address book with decay and ban list
	•	Periodic refresh with jitter and exponential backoff
	•	Metrics: seed-fetch success, signature failures, diversity score

⸻

10) Backwards/forwards compatibility
	•	version guards structural changes. Unknown fields MUST be ignored.
	•	Future versions may add:
	•	Per-entry cert fingerprints for QUIC ALPN pinning
	•	Geo hints to improve initial latency
	•	Rate-limit advisories

End of spec.
