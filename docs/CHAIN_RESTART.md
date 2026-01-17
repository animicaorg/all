# Animica chain restart (chain_id = 0)

## Summary
This release hard-resets the network identity and changes **mainnet `chain_id` from `1` to `0`**. Old nodes and old data **must not** be reused on the new chain.

## Required node version
Run a node version that includes the chain restart changes (network magic, genesis hash, and chain_id=0). Older nodes will be rejected at handshake and their tips are ignored.

## Reset / data safety
The node enforces genesis/chain identity checks at startup and will refuse to start if the existing DB is incompatible. You have two options:

1. **Reset the data dir** (recommended for operators):
   ```bash
   rm -rf ~/.animica/chain-0
   ```
   If you were previously on chain_id=1, the old data is in `~/.animica/chain-1` and will not be reused.

2. **Use the reset flag**:
   ```bash
   animica node up --auto-reset-genesis-mismatch
   ```
   or set:
   ```bash
   export ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1
   ```

## Verify the restart

1. **RPC chainId reports 0**:
   ```bash
   curl -s -X POST http://127.0.0.1:8547 \
     -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'
   ```

2. **P2P peers match the new identity** (chain_id=0, genesis hash, network magic, protocol version). Use the node logs or peer list to verify.

3. **Sync status shows fresh peer tips**:
   * `peer_tips_fresh` > 0
   * `sync_status_reason` is not `no_fresh_peer_tips`

## Operational checklist
- [ ] Update node binary/package.
- [ ] Reset data dir or enable auto-reset.
- [ ] Confirm RPC `chainId` is 0.
- [ ] Confirm peers share the new network identity and tips are fresh.
