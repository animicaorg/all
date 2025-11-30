# Useful work demo (devnet → VM(Py))

This walkthrough mines a devnet block, fetches it over JSON-RPC, and feeds the header data into a deterministic Python contract using the VM(Py) CLI.

## Prerequisites
- Python 3.11+
- Run commands from the repository root so `vm_py` and `aicf` are importable.
- `requests` installed (already present in the repo's Python requirements).

## One-shot demo

```bash
# Mines a block on the lightweight devnet shim, fetches it, and calls the contract.
python -m vm_py.examples.useful_work_demo
```

The script will:
1. Launch the JSON-RPC shim (`python -m aicf.node`) on `127.0.0.1:18545`.
2. Mine one block via `animica_generate` and fetch it with `eth_getBlockByNumber`.
3. Invoke `omni-vm-run` against `vm_py/examples/useful_work/manifest.json` with the block hash/height/timestamp/difficulty/miner encoded as arguments.

You should see JSON output from `omni-vm-run` that includes the block-derived score.

## Manual steps (if you prefer the CLI sequence)

```bash
# 1) Start the shim
python -m aicf.node --network devnet --rpc-addr 127.0.0.1 --rpc-port 18545 --datadir /tmp/devnet-demo &
RPC_PID=$!
sleep 1

# 2) Mine and fetch a block
curl -s -X POST http://127.0.0.1:18545/ -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"animica_generate","params":[1]}'
BLOCK=$(curl -s -X POST http://127.0.0.1:18545/ -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByNumber","params":["latest",false]}')

# 3) Extract arguments and run the VM contract
HASH=$(echo "$BLOCK" | jq -r '.result.hash')
HEIGHT=$(echo "$BLOCK" | jq -r '.result.number' | python - <<'PY'
import sys
print(int(sys.stdin.read(), 16))
PY)
STAMP=$(echo "$BLOCK" | jq -r '.result.timestamp' | python - <<'PY'
import sys
print(int(sys.stdin.read(), 16))
PY)
DIFF=$(echo "$BLOCK" | jq -r '.result.difficulty' | python - <<'PY'
import sys
print(int(sys.stdin.read(), 16))
PY)
MINER=$(echo "$BLOCK" | jq -r '.result.miner')

python -m vm_py.cli.run \
  --manifest vm_py/examples/useful_work/manifest.json \
  --call score_block \
  --args "[\"$HASH\", $HEIGHT, $STAMP, $DIFF, \"$MINER\"]"

kill $RPC_PID
```

## Contract source
See `vm_py/examples/useful_work/contract.py` for the deterministic scoring logic. It expects bytes for the block hash and miner, plus integers for height/timestamp/difficulty.
