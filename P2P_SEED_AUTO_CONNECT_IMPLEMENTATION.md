# P2P Auto-Connect Seed Nodes Implementation

## Summary

Implemented automatic P2P seed node connectivity with network-specific domains and IP fallback. When users start their nodes, the P2P stack automatically connects to seed nodes based on the network (mainnet/testnet/devnet) with built-in DNS and IP fallback mechanisms.

## Changes Made

### 1. Network-Specific Seed Configuration (`p2p/config.py`)

**Added:**
- `DEFAULT_SEEDS_BY_NETWORK`: Dictionary mapping chain_id to seed addresses
  - Chain ID 1 (Mainnet): `mainnet.animica.org`
  - Chain ID 2 (Testnet): `testnet.animica.org`  
  - Chain ID 1337 (Devnet): `devnet.animica.org`
  - All include IP fallback: `144.126.133.21`
  
- `NETWORK_NAME_TO_CHAIN_ID`: Maps network names to chain IDs
  - "mainnet" → 1
  - "testnet" → 2
  - "devnet" → 1337

**Updated:**
- `_load_seeds_from_env()`: Now accepts `chain_id` parameter and respects `ANIMICA_P2P_NETWORK` environment variable
- `load_config()`: Reads `ANIMICA_P2P_CHAIN_ID` to select network-specific seeds

**Priority order:**
1. `ANIMICA_P2P_SEEDS` (explicit seed list) - highest priority
2. `ANIMICA_P2P_NETWORK` (network name)
3. `ANIMICA_P2P_CHAIN_ID` (chain ID)
4. `DEFAULT_SEEDS` (legacy fallback)

### 2. Embedded Fallback Seeds (`p2p/discovery/seeds.py`)

**Added:**
- `EMBEDDED_FALLBACK_SEEDS`: Primary IP fallback seeds
  ```python
  [
      "quic://144.126.133.21:443",
      "tcp://144.126.133.21:30333"
  ]
  ```

- `NETWORK_DNS_SEEDS`: DNS TXT record names for discovery
  ```python
  {
      1: "_p2p.mainnet.animica.org",
      2: "_p2p.testnet.animica.org",
      1337: "_p2p.devnet.animica.org"
  }
  ```

- `NETWORK_HTTPS_SEEDS`: HTTPS JSON endpoints for discovery
  ```python
  {
      1: "https://seeds.mainnet.animica.org/seeds.json",
      2: "https://seeds.testnet.animica.org/seeds.json",
      1337: "https://seeds.devnet.animica.org/seeds.json"
  }
  ```

- `discover_for_network()`: New async function to discover seeds for a specific chain_id

**Updated:**
- `discover_all()`: Added `include_fallbacks` parameter (default: True)
  - Automatically includes embedded fallback seeds if DNS/HTTPS discovery fails

### 3. Node Service Auto-Connect (`p2p/node/service.py`)

**Updated:**
- `_seed_and_discover()`: 
  - Uses seeds directly from config (already network-specific)
  - Falls back to `discover_for_network()` if no seeds configured
  - Extracts chain_id from deps to enable automatic discovery
  - Converts SeedEndpoints to multiaddr strings for dialing

### 4. Seed JSON Files (`ops/seeds/`)

**Updated all seed files with new structure:**

- `mainnet.json`: 
  - Primary seed with `mainnet.animica.org` DNS
  - IP fallback `144.126.133.21`
  - Both QUIC (UDP 443) and TCP (30333)

- `testnet.json`:
  - Primary seed with `testnet.animica.org` DNS
  - IP fallback `144.126.133.21`
  - Both QUIC and TCP

- `devnet.json`:
  - Primary seed with `devnet.animica.org` DNS
  - IP fallback `144.126.133.21`
  - Additional regional seeds (US, EU, APAC)

- `bootstrap_nodes.json`:
  - Added primary Animica seed entry
  - Preserved existing regional seeds

### 5. Documentation Updates

**`ops/seeds/README.md`:**
- Added "Default seed configuration" section
- Documented network-specific domains
- Documented fallback IP (144.126.133.21)
- Documented default ports (QUIC UDP 443, TCP 30333)
- Added environment variable documentation
- Included usage examples

**`p2p/fixtures/seed_list.txt`:**
- Updated with current seed structure
- Highlighted primary IP fallback
- Organized by network (mainnet/testnet/devnet)

### 6. Comprehensive Testing (`p2p/tests/test_network_seeds.py`)

**Test coverage:**
- Network-specific seed selection (mainnet, testnet, devnet)
- Environment variable handling (ANIMICA_P2P_NETWORK, ANIMICA_P2P_CHAIN_ID)
- Custom seed override behavior
- Embedded fallback seed functionality
- DNS/HTTPS seed discovery mappings
- Full config loading integration

**All 19 tests pass** ✓

## Usage

### Automatic (Default)

The P2P stack automatically selects seeds based on the active network:

```bash
# Node determines network from genesis/config
animica node up
```

### Via Network Name

```bash
# Use mainnet seeds
export ANIMICA_P2P_NETWORK=mainnet
animica node up

# Use testnet seeds
export ANIMICA_P2P_NETWORK=testnet
animica node up

# Use devnet seeds
export ANIMICA_P2P_NETWORK=devnet
animica node up
```

### Via Chain ID

```bash
# Mainnet (chain_id=1)
export ANIMICA_P2P_CHAIN_ID=1
animica node up

# Testnet (chain_id=2)
export ANIMICA_P2P_CHAIN_ID=2
animica node up
```

### Custom Seeds (Override)

```bash
# Use custom seeds instead of defaults
export ANIMICA_P2P_SEEDS="/ip4/1.2.3.4/tcp/30333,/dns4/custom.seed.com/udp/443/quic-v1"
animica node up
```

## Seed Configuration Details

### Seed Addresses by Network

**Mainnet (chain_id=1):**
```
/dns4/mainnet.animica.org/udp/443/quic-v1
/dns4/mainnet.animica.org/tcp/30333
/ip4/144.126.133.21/udp/443/quic-v1
/ip4/144.126.133.21/tcp/30333
```

**Testnet (chain_id=2):**
```
/dns4/testnet.animica.org/udp/443/quic-v1
/dns4/testnet.animica.org/tcp/30333
/ip4/144.126.133.21/udp/443/quic-v1
/ip4/144.126.133.21/tcp/30333
```

**Devnet (chain_id=1337):**
```
/dns4/devnet.animica.org/udp/443/quic-v1
/dns4/devnet.animica.org/tcp/30333
/ip4/144.126.133.21/udp/443/quic-v1
/ip4/144.126.133.21/tcp/30333
```

### Connection Flow

1. **Seed Selection:**
   - Check `ANIMICA_P2P_SEEDS` env var → use if set
   - Check `ANIMICA_P2P_NETWORK` env var → map to chain_id
   - Check `ANIMICA_P2P_CHAIN_ID` env var → use directly
   - Fall back to network detection from genesis/config

2. **DNS Resolution:**
   - Try DNS resolution for domain names (e.g., mainnet.animica.org)
   - If DNS succeeds, use resolved IP addresses

3. **IP Fallback:**
   - If DNS fails or times out, use IP address `144.126.133.21`
   - Embedded in code as `EMBEDDED_FALLBACK_SEEDS`

4. **Protocol Selection:**
   - Try QUIC first (UDP port 443) - preferred for performance
   - Fall back to TCP (port 30333) if QUIC fails

5. **Discovery:**
   - After initial connection, use Kademlia DHT for peer discovery
   - Periodic redial of seeds and discovered peers

## Security Considerations

- **Seeds are untrusted**: Only used for initial peer discovery
- **No data trust**: Never trust blocks/headers/txs from seeds alone
- **Domain security**: DNS resolution uses system resolver (DNSSEC if available)
- **IP fallback**: Hardcoded IP as last resort, but still untrusted
- **Rate limiting**: Seeds should rate-limit handshakes to prevent DoS
- **Diversity**: Multiple seeds across regions and ASNs for resilience

## Acceptance Criteria ✓

All requirements from the problem statement have been met:

1. ✓ Nodes automatically connect to seed nodes on startup
2. ✓ DNS resolution tried first (mainnet/testnet/devnet.animica.org)
3. ✓ IP fallback to 144.126.133.21 when DNS fails
4. ✓ Correct seeds selected based on chain_id (1/2/1337)
5. ✓ QUIC tried first (port 443), TCP fallback (port 30333)
6. ✓ P2P discovery continues after initial seed connection
7. ✓ No manual configuration required for basic connectivity

## Testing

Run seed configuration tests:
```bash
pytest p2p/tests/test_seed_config.py p2p/tests/test_network_seeds.py -v
```

Test configuration loading manually:
```bash
# Test mainnet
ANIMICA_P2P_NETWORK=mainnet python -m p2p.config | grep seeds

# Test testnet
ANIMICA_P2P_NETWORK=testnet python -m p2p.config | grep seeds

# Test devnet
ANIMICA_P2P_NETWORK=devnet python -m p2p.config | grep seeds
```

## Files Modified

- `p2p/config.py` - Network-specific seed configuration
- `p2p/discovery/seeds.py` - Embedded fallbacks and discovery helpers
- `p2p/node/service.py` - Auto-connect logic
- `ops/seeds/bootstrap_nodes.json` - Production seed entry
- `ops/seeds/mainnet.json` - Mainnet seed configuration
- `ops/seeds/testnet.json` - Testnet seed configuration
- `ops/seeds/devnet.json` - Devnet seed configuration
- `ops/seeds/README.md` - Updated documentation
- `p2p/fixtures/seed_list.txt` - Example seeds
- `p2p/tests/test_network_seeds.py` - Comprehensive test suite (new)

## Next Steps (Optional Future Enhancements)

1. **Seed Health Monitoring**: Implement automated health checks for seed nodes
2. **Regional Seed Selection**: Geo-IP based seed selection for lower latency
3. **Seed Discovery Cache**: Cache discovered peers to reduce dependency on seeds
4. **Telemetry**: Add metrics for seed connection success rates
5. **Dynamic Seed Updates**: Fetch seed lists from trusted sources periodically
