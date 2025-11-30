# Miner RPCs

These helper RPCs expose lightweight work templates and a submission endpoint suitable for pools or standalone miners.

## miner.getWork
Request fresh work (header sign bytes, mixSeed, and targets).

```bash
curl -X POST http://127.0.0.1:8545/rpc \
  -H 'Content-Type: application/json' \
  -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "miner.getWork"
      }'
```

Example response (truncated):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "jobId": "af...",
    "height": 42,
    "thetaMicro": 3000000,
    "shareTarget": 0.01,
    "target": "0x12ab...",
    "signBytes": "0x746f...",
    "hints": {"mixSeed": "0x..."},
    "header": {"number": 42, "parentHash": "0x..."}
  }
}
```

## miner.submitWork
Submit a solution for validation. Provide the `jobId` from `miner.getWork` and a nonce in hex form.

```bash
curl -X POST http://127.0.0.1:8545/rpc \
  -H 'Content-Type: application/json' \
  -d '{
        "jsonrpc": "2.0",
        "id": 2,
        "method": "miner.submitWork",
        "params": {"jobId": "af...", "nonce": "0x0000000000000001"}
      }'
```

Successful submissions return a `hash`, the accepted `height`, and a `newHead` view. Invalid parameters or stale jobs surface as JSON-RPC errors with `code` -32602.
