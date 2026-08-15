# Manual integration test — Base Sepolia, real chain, real settlement

The automated suite (`node --test test/`) never touches a network. This is
the OPTIONAL manual pass that proves the self-hosted facilitator settles a
real EIP-3009 transfer on a real chain — run it on **Base Sepolia first**;
only after it passes end-to-end is the mainnet runbook step (README
"Ops / runbook") allowed to proceed.

Everything below runs on one machine against `sepolia.base.org`. Nothing
here touches the live `animica-x402.service`, nginx, or `/etc` — it uses a
private env file and throwaway ports.

## 0. What you need

| Thing | Where |
|---|---|
| Facilitator wallet (fresh throwaway key) | generate below; needs ~0.001 Base Sepolia ETH for gas |
| Base Sepolia ETH faucet | e.g. the official Base faucet list (docs.base.org/docs/tools/network-faucets) |
| Payer wallet (fresh throwaway key) | `node test/manual/smoke-pay.mjs keygen` |
| Base Sepolia USDC for the payer | https://faucet.circle.com (select Base Sepolia; token `0x036CbD53842c5426634e7929541eC2318f3dCF7e`) |
| A settlement address | any address you control (can be the facilitator address for the test; MUST be a separate cold address on mainnet) |

Both keys in this test are throwaways: generated for this run, funded with
testnet-only value, never reused on mainnet.

## 1. Environment (private file, NOT /etc)

```sh
cd apps/x402-gateway
umask 077
cat > /tmp/x402-sepolia.env <<'EOF'
ANM_X402_ENABLED=1
X402_ENV=development

# --- facilitator (self-hosted, Sepolia) ---
X402_NETWORK=base-sepolia
X402_RPC_URL=https://sepolia.base.org
X402_SETTLEMENT_ADDRESS=<0x… address that receives the USDC>
X402_FACILITATOR_PRIVATE_KEY=<32-byte hex of the throwaway facilitator key>
X402_DB_PATH=/tmp/x402-sepolia.db
X402_MIN_GAS_BALANCE_WEI=100000000000000   # 0.0001 ETH readyz floor for the test

# --- gateway offers (must agree with the facilitator) ---
X402_NETWORK_EVM=eip155:84532
X402_BASE_PAYTO=<same address as X402_SETTLEMENT_ADDRESS>
X402_FACILITATOR_MODE=self
X402_GATEWAY_DB_PATH=/tmp/x402-sepolia-gateway.db
X402_RECEIPT_HMAC_KEY=manual-test-only
EOF
```

Generate the facilitator key (run yourself, never commit the output):

```sh
node -e 'const s=require("@noble/secp256k1");console.log(Buffer.from(s.utils.randomSecretKey()).toString("hex"))'
# derive its address (the thing you fund with Sepolia ETH):
X402_FACILITATOR_PRIVATE_KEY=<hex> node -e '
  const {loadSigner}=require("./src/facilitator-evm/key");
  console.log(loadSigner(process.env.X402_FACILITATOR_PRIVATE_KEY).address)'
```

Fund that address with a little Base Sepolia ETH (one settlement costs
~0.0000005 ETH on Sepolia; 0.001 ETH is hundreds of runs).

## 2. Start both processes

```sh
# terminal 1 — facilitator (verifies the LIVE DOMAIN_SEPARATOR() at boot;
# a wrong network/token config refuses to start):
set -a; . /tmp/x402-sepolia.env; set +a
node src/facilitator-evm/server.js

# terminal 2 — gateway:
set -a; . /tmp/x402-sepolia.env; set +a
node src/server.js
```

Sanity before paying:

```sh
curl -s http://127.0.0.1:8743/readyz | python3 -m json.tool   # every check true
curl -s http://127.0.0.1:8743/supported                        # eip155:84532 + signer addr
curl -s http://127.0.0.1:8742/x402 | python3 -m json.tool      # catalog, live available flags
```

`readyz` failing tells you exactly what is wrong (rpc / chain_id /
usdc_domain / db / gas_balance) — fix before continuing.

## 3. The payer flow (echo first, then qrng)

```sh
node test/manual/smoke-pay.mjs keygen        # throwaway payer; fund it at faucet.circle.com

export SMOKE_PRIVATE_KEY=<key printed above>
export SMOKE_NETWORK=eip155:84532

# 3a. dev echo — the settlement smoke marker
node test/manual/smoke-pay.mjs http://127.0.0.1:8742/x402/paid/echo

# 3b. the first real product
node test/manual/smoke-pay.mjs http://127.0.0.1:8742/x402/qrng/draw?bytes=32
```

Expected output of each run: the 402 offer (amount/asset/payTo), the signed
nonce, then `HTTP 200`, `settlement success=true`, a real tx hash and a
`https://sepolia.basescan.org/tx/…` link — open it and check the
`AuthorizationUsed` + `Transfer` logs carry your payer, the payTo and the
exact amount. The qrng body must contain `randomness`, `health.passed:
true`, the `attestation` block and `verification` rules (today with
`attested: false` — that is the honest current truth, see docs/x402.md).

Note: the qrng draw requires the local Animica node RPC
(`X402_ANIMICA_RPC_URL`, default `http://127.0.0.1:8545/rpc`) to be
reachable from the gateway — on a box without the node the catalog will
truthfully report `qrng` unavailable and the endpoint answers 503 without
ever taking payment (which is itself a pass of the availability gate).
Echo (3a) has no node dependency.

## 4. Ledger + reconciliation checks

```sh
X402_DB_PATH=/tmp/x402-sepolia.db bin/animica-x402 settlements list
X402_DB_PATH=/tmp/x402-sepolia.db bin/animica-x402 revenue --since 1h
X402_DB_PATH=/tmp/x402-sepolia.db \
X402_RPC_URL=https://sepolia.base.org \
X402_SETTLEMENT_ADDRESS=<payTo> bin/animica-x402 reconcile
```

`reconcile` must print `OK` for every settled row (status 1 +
AuthorizationUsed + Transfer log match). Any `MISMATCH`/`NO_RECEIPT` is a
stop-ship finding.

## 5. Replay + double-spend checks (manual)

1. **Same authorization twice.** Capture the exact `payment-signature`
   header of a successful run (add `-v` style logging or re-encode it), then
   re-send it with curl:

   ```sh
   curl -si -H "payment-signature: <captured b64>" \
     http://127.0.0.1:8742/x402/paid/echo | head -5
   ```

   Expected: `402` (verify fails — the nonce is consumed on-chain and in the
   DB; `x402_replays_rejected_total` on the facilitator `/metrics` bumps).
   The smoke script itself never replays: it draws a fresh random nonce per
   run, so simply running it twice tests two independent payments instead.

2. **Idempotent replay (no second charge).** Run a paid request with an
   `Idempotency-Key`, then repeat the exact same request (same key, same
   captured payment header):

   ```sh
   SMOKE_IDEMPOTENCY_KEY=manual-1 node test/manual/smoke-pay.mjs …
   # then re-send the captured payment-signature + idempotency-key with curl
   ```

   Expected: second response is `200` with `idempotent-replay: true` and the
   ORIGINAL settlement header; `settlements list` still shows exactly one
   settled row for that authorization.

3. **Restart survival.** Stop the facilitator, start it again, retry the
   replay from (1): still rejected — the ledger is on disk, not in memory.

## 6. Cleanup

```sh
rm -f /tmp/x402-sepolia.env /tmp/x402-sepolia.db* /tmp/x402-sepolia-gateway.db*
unset SMOKE_PRIVATE_KEY
```

The throwaway keys are burned with the files. Only after every step above
passes may the mainnet promotion runbook (README) be attempted — same
sequence, `X402_NETWORK=base`, real funded wallet, real 0600 env file in
`/etc/animica-x402.env`, and the systemd/nginx files from `systemd/` and
`nginx/`.
