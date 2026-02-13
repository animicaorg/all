# Animica Transaction RPC Canonical Schema (CLI-aligned)

This wallet extension follows the exact JSON-RPC call shape used by `animica tx send`.

## Submit transaction

- **Method:** `tx.sendRawTransaction`
- **Params shape:** positional array
- **Request body:**

```json
{
  "jsonrpc": "2.0",
  "id": 123,
  "method": "tx.sendRawTransaction",
  "params": ["0x<canonical-cbor-envelope>"]
}
```

## Encoding rules

- `rawTx` MUST be a `0x`-prefixed lowercase/uppercase hex string.
- ANM amounts are converted in UI to base units (`1 ANM = 1_000_000_000`) using `BigInt` math.
- Base-unit integers MUST NOT use JS floating-point numbers in signing payloads.
- Signing context MUST use:
  - `chain_id = 1` (mainnet)
  - `domain = "tx"`
  - `prehash = "sha3-512"`

## Signature envelope compatibility

Node accepts envelope signatures containing fields equivalent to CLI output:
- `algId`
- `pk`/`pubkey`
- `sig`
- `domain`
- `prehash`
- `chainId`
