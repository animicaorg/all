# Mainnet Genesis Checklist

Follow these steps to validate a freshly built Animica mainnet genesis before launch.

1. **Install the mainnet genesis locally**
   - Run: `bash genesis/use.sh mainnet`
2. **Start a node with the mainnet profile**
   - Run: `ops/run.sh --profile mainnet node`
3. **Verify the chain ID matches mainnet (1)**
   - Run: `animica-node status --rpc-url $ANIMICA_RPC_URL | jq -r '.chainId'`
   - Expected output: `1`
4. **Verify genesis premine balances**
   - Foundation: `animica-node balance --address system:foundation --rpc-url $ANIMICA_RPC_URL`
   - Treasury: `animica-node balance --address system:treasury --rpc-url $ANIMICA_RPC_URL`
   - AICF: `animica-node balance --address system:aicf --rpc-url $ANIMICA_RPC_URL`
   - Founder allocation: `animica-node balance --address system:founder --rpc-url $ANIMICA_RPC_URL`
   - Confirm they match docs/anm_tokenomics_mainnet.md (45M / 20M / 7M / 9M ANM respectively).
5. **Verify block reward on the first mined block**
   - Wait for block 1, then run: `animica-node block --height 1 --rpc-url $ANIMICA_RPC_URL | jq '.block.reward'`
   - Expected values: initial reward **5,194,100,000 nANM** (5.1941 ANM) split per policy.
