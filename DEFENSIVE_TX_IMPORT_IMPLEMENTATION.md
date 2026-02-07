# Defensive Transaction Import Implementation

## Overview

The transaction import system now uses a defensive multi-format decoding approach that tries multiple methods when a transaction fails to decode with the primary decoder.

## Implementation

### Primary Entry Point: `_decode_tx_defensive()`

Located in `rpc/methods/tx.py`, this function is now the main entry point for all transaction decoding:

```python
def _decode_tx_defensive(raw: bytes) -> tuple[t.Any, dict]:
    """
    Defensive transaction decoder that tries multiple decoding methods.
    
    This is the primary entry point for transaction decoding. It tries:
    1. Primary CBOR decoder (core.encoding.cbor)
    2. Alternative CBOR decoders (cbor2, msgspec)
    3. JSON fallback (if data appears to be JSON)
    
    If all methods fail, raises InvalidTx with details about all attempted methods.
    """
```

### Decoding Sequence

When a transaction is received, the system attempts to decode it using the following sequence:

1. **Primary CBOR Decoder** (`core.encoding.cbor.loads`)
   - This is the canonical, preferred decoder
   - If successful, processing continues immediately
   - If it fails, the system logs the failure and tries alternatives

2. **Alternative CBOR Decoders**
   - **cbor2**: Python library for CBOR decoding
   - **msgspec**: High-performance serialization library with CBOR support
   - Each is tried in sequence until one succeeds

3. **JSON Fallback**
   - If the data appears to be JSON (starts with `{` or `[`)
   - Useful for debugging and alternative client implementations
   - Not the standard format but supported for robustness

### Error Reporting

When all decoders fail, the system generates a comprehensive error message that includes:

- List of all attempted decoders
- Specific error message from each decoder
- Hints about proper transaction format

Example error output:

```
Transaction decode failed after trying all available decoders

Attempted decoders and their failures:
  - core.encoding.cbor (primary): ValueError: truncated data
  - cbor2: library not available
  - msgspec: DecodeError: invalid msgpack
  - json: not valid UTF-8: 'utf-8' codec can't decode byte 0xff

Ensure the transaction is properly encoded as CBOR with structure: {body: {...}, sigs: [...]}
```

## Usage Points

The defensive decoder is used in the following locations:

1. **RPC Transaction Submission** (`tx.sendRawTransaction`)
   - Main entry point for user-submitted transactions
   - Uses `_decode_tx_defensive()` directly

2. **Transaction Decoding** (`tx.decodeRawTransaction`)
   - Debug endpoint for transaction inspection
   - Uses defensive decoding

3. **Transaction Verification** (`tx.debugVerifyRawTransaction`)
   - Signature verification endpoint
   - Uses defensive decoding

4. **Transaction Retrieval** (`tx.getTransactionByHash`)
   - When reading transactions from mempool
   - Uses defensive decoding

## Benefits

1. **Robustness**: System can handle transactions encoded with different CBOR libraries
2. **Debugging**: Clear error messages help identify encoding issues
3. **Compatibility**: Supports alternative formats during development/testing
4. **Observability**: Logs which decoder succeeded (when fallback is used)

## Performance Considerations

- **Zero overhead in happy path**: Primary decoder succeeds immediately
- **Minimal overhead on failure**: Alternative decoders only tried when primary fails
- **No permanent performance impact**: Failed transactions are rejected quickly

## Testing

See `tests/test_defensive_tx_import.py` for comprehensive test coverage of:
- Primary decoder success
- Fallback decoder success
- All decoders failing
- Error message format
- Logging behavior

## Configuration

Currently, the defensive import system does not require configuration. It automatically detects available alternative decoders at import time.

Future configuration options could include:
- Enabling/disabling specific fallback decoders
- Metrics collection for decoder usage
- Custom decoder plugins

## Metrics

The system tracks decoder fallback usage through:
- Log messages when fallback decoders are used
- Existing `TX_VALIDATION_FAILURES` counter (reason="cbor_decode_failed")
- `TX_DECODER_SUCCESS` counter with decoder label - tracks successful decodes by decoder type
- `TX_DECODER_FALLBACK` counter with fallback_decoder label - tracks when fallback decoders are used  
- `TX_DECODER_ALL_FAILED` counter - tracks when all decoders fail

These metrics are defined in `rpc/metrics.py` and help monitor:
- Which decoder is being used in production (should be "primary" in vast majority of cases)
- How often fallback decoders are needed (indicates encoding compatibility issues)
- Complete decoder failure rate (indicates invalid transactions being submitted)
