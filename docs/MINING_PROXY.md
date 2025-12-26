# Mining with RPC Proxy (DEPRECATED)

⚠️ **DEPRECATED**: This document describes a legacy proxy mechanism that is **disabled by default**.

**For production**: Use P2P-first networking instead. See [P2P Sync Guide](p2p_sync.md).

## Overview

The mining proxy feature was a legacy mechanism that forwarded mining operations to an external RPC endpoint. This approach is **no longer recommended** and is disabled by default in favor of decentralized P2P consensus.

**Current behavior**: All nodes perform local validation via P2P networking by default.

## Key Features

1. **Trusted Source of Truth**: All mining operations are validated against `http://127.0.0.1:8545/rpc` by default
2. **Automatic Retry Logic**: Transient failures are handled with configurable retry attempts
3. **Fallback Mechanism**: Automatically falls back to local node if trusted endpoint is unreachable
4. **Enhanced Logging**: Detailed logging for debugging and monitoring
5. **Backward Compatibility**: Existing mining workflows continue to work without changes

## Usage

### Basic Mining (with proxy enabled by default)

```bash
# Mine 5 blocks to a wallet label
animica miner mine-blocks --count 5 premine

# Mine to a specific address
animica miner mine-blocks --count 10 anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
```

By default, mining operations use the proxy to validate against `http://127.0.0.1:8545/rpc`.

### Disable Proxy

To mine directly without proxy validation:

```bash
animica miner mine-blocks --count 5 premine --no-proxy
```

### Verbose Mode

Enable verbose output to see proxy operation details:

```bash
animica miner mine-blocks --count 5 premine --verbose
```

Output includes:
- Proxy configuration (retries, delays, timeout)
- Forwarding status to trusted RPC
- Fallback activation (if needed)
- Transaction details

## Configuration

The proxy can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIMICA_TRUSTED_RPC_URL` | `http://127.0.0.1:8545/rpc` | Trusted RPC endpoint for validation |
| `ANIMICA_PROXY_MAX_RETRIES` | `3` | Maximum retry attempts on failure |
| `ANIMICA_PROXY_RETRY_DELAY_MS` | `1000` | Delay between retries (milliseconds) |
| `ANIMICA_PROXY_TIMEOUT_SECONDS` | `30.0` | Request timeout (seconds) |
| `ANIMICA_PROXY_ENABLE_CACHE` | `false` | Enable response caching (future feature) |

### Example Configuration

```bash
# Use custom trusted endpoint
export ANIMICA_TRUSTED_RPC_URL="https://custom-rpc.example.com"

# Increase retry attempts and timeout for slow networks
export ANIMICA_PROXY_MAX_RETRIES=5
export ANIMICA_PROXY_TIMEOUT_SECONDS=60.0

# Mine with custom configuration
animica miner mine-blocks --count 5 premine
```

## Proxy Behavior

### Normal Operation

1. Mining request is sent to the proxy
2. Proxy forwards request to trusted RPC (`http://127.0.0.1:8545/rpc`)
3. Response is returned to the CLI
4. Mining completes successfully

### Retry Logic

When the trusted RPC is temporarily unreachable:

1. First attempt fails
2. Proxy waits for `ANIMICA_PROXY_RETRY_DELAY_MS` milliseconds
3. Retries the request (up to `ANIMICA_PROXY_MAX_RETRIES` times)
4. If successful on retry, mining continues
5. If all retries fail, fallback is activated

### Fallback Mechanism

When all retry attempts to trusted RPC fail:

1. Proxy invokes fallback handler
2. Fallback uses local RPC node specified by `--rpc-url` or `ANIMICA_RPC_URL`
3. Mining completes via local node
4. Warning is logged about fallback usage

### Error Handling

If both trusted RPC and local fallback fail:

1. Error is logged with details from both attempts
2. Mining operation exits with error code 5
3. User is notified of the failure

## Use Cases

### Home Miner

A home user mining on their local machine:

```bash
# Local node running on default port
# Proxy validates against 127.0.0.1
# Falls back to local node if needed
animica miner mine-blocks --count 10 my-wallet
```

### Mining Pool

A mining pool operator routing work to multiple nodes:

```bash
# Disable proxy for pool internal coordination
# Pool manages its own validation
animica miner mine-blocks --count 100 pool-address --no-proxy
```

### Testnet Development

Developer testing on testnet:

```bash
# Point to testnet trusted RPC
export ANIMICA_TRUSTED_RPC_URL="https://testnet-127.0.0.1"
animica miner mine-blocks --count 5 test-wallet --verbose
```

## Monitoring and Debugging

### Enable Verbose Logging

```bash
# CLI verbose mode
animica miner mine-blocks --count 5 premine --verbose

# Python logging
export ANIMICA_LOG_LEVEL=DEBUG
animica miner mine-blocks --count 5 premine
```

### Check Proxy Status

Verbose output shows:
- Proxy enabled/disabled
- Trusted RPC URL
- Max retries and timeout settings
- Forwarding attempts and outcomes
- Fallback activation

### Common Issues

**Issue**: Proxy always timing out
```bash
# Solution: Increase timeout
export ANIMICA_PROXY_TIMEOUT_SECONDS=60.0
```

**Issue**: Too many retries causing delays
```bash
# Solution: Reduce retry attempts
export ANIMICA_PROXY_MAX_RETRIES=1
```

**Issue**: Want to bypass proxy entirely
```bash
# Solution: Use --no-proxy flag
animica miner mine-blocks --count 5 premine --no-proxy
```

## API Reference

### RpcProxy Class

Python module: `rpc.proxy`

```python
from rpc.proxy import create_proxy, ProxyConfig

# Create proxy with default config
proxy = create_proxy()

# Create proxy with custom config
config = ProxyConfig(
    trusted_rpc_url="https://custom-rpc.example.com",
    max_retries=5,
    retry_delay_ms=2000,
    timeout_seconds=60.0,
)
proxy = create_proxy(config)

# Forward async request
result = await proxy.forward_request(
    "miner.mine",
    {"count": 1, "address": "anim1..."},
    fallback_handler=my_fallback_fn,
)

# Forward sync request (from non-async context)
result = proxy.sync_forward_request(
    "miner.mine",
    {"count": 1, "address": "anim1..."},
)
```

### ProxyConfig

Configuration dataclass for RPC proxy:

```python
@dataclass
class ProxyConfig:
    trusted_rpc_url: str = "http://127.0.0.1:8545/rpc"
    max_retries: int = 3
    retry_delay_ms: int = 1000
    timeout_seconds: float = 30.0
    enable_caching: bool = False
    
    @classmethod
    def from_env(cls) -> ProxyConfig:
        """Load config from environment variables"""
```

## Testing

Comprehensive test coverage is provided:

### Proxy Module Tests

```bash
pytest tests/unit/rpc/test_proxy.py -v
```

Tests include:
- Request forwarding
- Retry logic
- Fallback handling
- Error scenarios
- Configuration loading

### Mining CLI Tests

```bash
pytest python/animica/cli/tests/test_mining_proxy.py -v
```

Tests include:
- Proxy enabled by default
- Proxy disabled with --no-proxy
- Fallback activation
- Verbose output
- Import failure handling

### Backward Compatibility

```bash
pytest python/animica/cli/tests/test_mining_cli.py -k mine_blocks -v
```

Ensures existing mining tests continue to pass.

## Security Considerations

1. **Trusted Endpoint**: `127.0.0.1` is operated by the Animica Foundation and serves as the canonical source of truth
2. **TLS/HTTPS**: All communication with trusted RPC uses HTTPS for encryption and authentication
3. **Fallback Safety**: Fallback to local node only occurs after all retry attempts fail
4. **No Credentials**: Proxy does not store or transmit any private keys or credentials

## Future Enhancements

Planned improvements:

1. **Response Caching**: Cache chain state and block templates to reduce RPC load
2. **Multiple Trusted Endpoints**: Support fallback between multiple trusted RPCs
3. **Metrics Export**: Prometheus metrics for proxy performance monitoring
4. **WebSocket Support**: Real-time streaming for mining templates
5. **Load Balancing**: Distribute requests across multiple trusted endpoints

## Support

For issues or questions:

- GitHub: https://github.com/animicaorg/all/issues
- Discord: https://discord.gg/animica
- Docs: https://docs.animica.org

## Related Documentation

- [Mining Guide](../README.md#mining)
- [RPC API Reference](./rpc/README.md)
- [Network Configuration](../python/animica/config.py)
- [CLI Commands](../ANIMICA_CLI_SUMMARY.md)
