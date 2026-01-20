# P2P Service Selection

This repository provides two P2P service implementations:

## 1. Legacy P2P Service (Default)
- **Location**: `p2p/node/p2p_service_legacy.py`
- **Size**: 16,000+ lines of code
- **Status**: Battle-tested, production-ready monolith
- **Description**: A comprehensive, self-contained P2P implementation that has been used in production. This is a mature, stable implementation with extensive features for peer management, sync, and network communication.

## 2. Modern P2P Service
- **Location**: `p2p/node/service.py`
- **Status**: Modern architecture using NodeService
- **Description**: A newer implementation that wraps the modern NodeService architecture with modular components.

## Configuration

You can choose which P2P service to use via the `ANIMICA_P2P_USE_LEGACY` environment variable:

### Use Legacy Service (Default)
```bash
# Explicitly enable legacy service
export ANIMICA_P2P_USE_LEGACY=1

# Or omit the variable (legacy is the default)
unset ANIMICA_P2P_USE_LEGACY
```

### Use Modern Service
```bash
# Disable legacy to use modern service
export ANIMICA_P2P_USE_LEGACY=0
```

## Implementation Details

The service selection happens in `rpc/deps.py` during P2P service initialization:

```python
use_legacy_p2p = _bool_env("ANIMICA_P2P_USE_LEGACY", True)  # Default to legacy
if use_legacy_p2p:
    from p2p.node.p2p_service_legacy import P2PService
    log.info("Using legacy P2P service (16k+ line monolith)")
else:
    from p2p.node.service import P2PService
    log.info("Using modern P2P service (NodeService wrapper)")
```

Both implementations provide the same interface:
- `__init__(listen_addrs, seeds, chain_id, deps, peerstore_path, ...)`
- `async start()`
- `async stop()`
- Peer management and network communication features

## Recommendation

**For production use**: Use the legacy service (default). It is well-tested and stable.

**For development/testing**: You can experiment with the modern service by setting `ANIMICA_P2P_USE_LEGACY=0`.

## Related Files

- `p2p/node/p2p_service_legacy.py` - Legacy 16k+ line implementation
- `p2p/node/service.py` - Modern NodeService-based implementation
- `rpc/deps.py` - Service selection logic
- `p2p/node/__init__.py` - Module documentation
