# How to verify tx send and mempool admission

```bash
animica node up --network mainnet
animica node status --network mainnet
animica wallet show <sender_address> --network mainnet
ANIMICA_DEBUG_TX=1 ANIMICA_DEBUG_MEMPOOL=1 animica tx send --network mainnet --from <sender> --to <recipient> --value 10
animica mempool list --network mainnet
animica miner mine-blocks --network mainnet --count 1 --address <miner_address>
animica node tx --network mainnet <txhash>
```

If mainnet funding is unavailable, run the same flow on local devnet:

```bash
animica node up --network devnet
animica node status --network devnet
animica wallet show <sender_address> --network devnet
ANIMICA_DEBUG_TX=1 ANIMICA_DEBUG_MEMPOOL=1 animica tx send --network devnet --from <sender> --to <recipient> --value 10
animica mempool list --network devnet
animica miner mine-blocks --network devnet --count 1 --address <miner_address>
animica node tx --network devnet <txhash>
```
