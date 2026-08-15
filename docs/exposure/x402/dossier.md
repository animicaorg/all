# Animica x402 — directory / listing dossier

Reusable, factual submission copy for x402 indexers, agent-API directories and MCP
registries. **Every field here was verified live on 2026-08-15** against the running
gateway, the public URLs and Base mainnet — nothing is from memory. Re-verify before any
submission (`node scripts/check-x402-discovery.mjs`), and never paste a claim this file
does not contain.

Rules that bind this copy:

- Randomness is described as **verifiable** (recomputable + signed). It is **not**
  hardware-attested: the serving node runs a software CSPRNG fallback and a software
  signer, so `source.is_quantum: false` and `attestation.attested: false` today. Never
  submit copy that says hardware QRNG or hardware attestation is live.
- Priority inference is **capacity-gated and currently unavailable**. Never list it as a
  buyable product while the catalog says `available: false`.
- `/x402/paid/echo` is a development-only settlement smoke route, disabled in production.
  **Never list it as a product** (see the x402scan section — it is the one resource that
  was registered early and must be replaced).
- No Coinbase service, account, API, SDK or URL is used or referenced anywhere (standing
  operator directive, 2026-08-15). The bazaar-format discovery metadata inside our 402
  responses is open-spec protocol metadata, not a Coinbase service, and stays.
- Prices are quoted from the live registry catalog. If a directory form needs a price,
  read `/.well-known/x402` at submission time rather than trusting this table.

---

## 1. Identity

| Field | Value |
|---|---|
| Name | **Animica x402** |
| Provider / organisation | Animica |
| Homepage | https://animica.org |
| Product page (human) | https://animica.dev/x402 |
| Discovery URL (machine) | **https://animica.dev/.well-known/x402** |
| OpenAPI 3.1 | https://animica.dev/x402/openapi.json |
| Aggregate stats | https://animica.dev/x402/stats |
| Health | https://animica.dev/x402/healthz |
| Contact | ai@3vdc.com |
| License | Apache-2.0 |
| Category tags | x402, pay-per-request API, agent payments, USDC, Base, randomness/QRNG, blockchain data API |

`GET https://animica.dev/x402` is content-negotiated: `Accept: text/html` gets the landing
page, every other client gets the same JSON catalog served at `/.well-known/x402`.

## 2. Short description (≤ 200 chars, QRNG lead)

> Pay $0.01 USDC via x402 for a verifiable Animica randomness draw — signed, health-checked,
> recomputable. Plus bulk L1 chain data. No account, no API key.

Alternate one-liner where a directory allows more room:

> Verifiable randomness for $0.01 per request — pay per call with USDC on Base over x402,
> no account, no API key, no subscription.

## 3. Long description

> Animica provides x402-paid machine APIs on Base, including verifiable randomness, bulk
> post-quantum L1 chain data, and priority AI inference.
>
> The lead product is a randomness draw for $0.01 USDC. Every draw returns the raw bytes
> together with the entropy source, an SP 800-90B health report, and an ed25519 attestation
> over the sha3-256 digest of exactly those bytes, so a buyer can verify that the serving
> node signed the bytes it delivered: check `attestation.digest_hex ==
> sha3_256(bytes(randomness))` and `ed25519_verify(public_key_hex, raw_bytes(digest_hex),
> signature_hex)`. Derived products — uniform integers (rejection sampling, no modulo
> bias), Fisher–Yates shuffles, weighted picks, bulk draws and commit–reveal — are computed
> from one verified draw by published rules, so the buyer can recompute the output
> themselves. The commit–reveal disclosure endpoint is free and public, because a
> commitment nobody can audit is worthless.
>
> "Verifiable" here means recomputable and signed, **not** hardware-attested: Animica's
> quantum-randomness service is running its software CSPRNG fallback on the serving node
> today, so every response reports `source.is_quantum: false` and `attestation.attested:
> false`. Those exact fields are published free in the discovery catalog before purchase and
> ride verbatim on every paid response; if a hardware provider is ever connected the same
> fields report it without any copy being rewritten.
>
> Bulk chain data sells range exports, batching, formats and an account-history index over
> the Animica L1 (a post-quantum proof-of-work chain, ML-DSA-65 signatures). Single lookups
> stay free forever on the public node JSON-RPC and explorer REST API; every exported row
> carries the block height and hash it came from, so anything bought can be re-verified for
> nothing. Priority AI inference exists but is gated on live serving capacity — while
> capacity is below the floor the catalog reports it unavailable and the endpoint returns
> 503 without requesting payment, because Animica does not take money for a service it
> knows it cannot deliver.
>
> Payment is the open x402 protocol: an unpaid request returns HTTP 402 with the exact
> terms, the client signs a USDC authorization locally, and the retry is served after
> on-chain settlement. Animica runs its own facilitator — there is no third-party
> settlement dependency — and never sees a payer's private key.

## 4. Network, asset, protocol

| Field | Value |
|---|---|
| Payment protocol | x402, `x402Version: 2` wire (v1 JSON-body compatibility retained) |
| Scheme | `exact` |
| Network | Base mainnet — `eip155:8453`, chain id **8453**, v1 slug `base` |
| Asset | **USDC** `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, 6 decimals |
| Settlement address (`payTo`) | `0x20fEee2dC0d4b36f69ddca69d0cE32d7E80b27a6` |
| Settlement mechanism | EIP-3009 `transferWithAuthorization`; Animica sponsors gas, the payer spends only USDC |
| Facilitator | self-hosted by Animica (`X402_FACILITATOR_MODE=self`), loopback-bound, never publicly exposed |
| Discovery metadata | `/.well-known/x402` + open-spec bazaar-format `extensions.bazaar.info.{input,output}` in every 402 |

Verified 2026-08-15: `GET https://animica.dev/x402/qrng/draw` → **HTTP 402** with
`payment-required` (base64 v2 JSON) and a v1-compatible body whose single `accepts` entry is
`{scheme:"exact", network:"base", maxAmountRequired:"10000", asset:"0x833589fC…02913",
payTo:"0x20fEee2d…27a6", resource:"https://animica.dev/x402/qrng/draw",
maxTimeoutSeconds:60}`.

## 5. Products and prices (from the live registry)

Read `/.well-known/x402` for the authoritative list; this snapshot is 2026-08-15.

| id | Product | Endpoint(s) | Price (USDC) | Live status |
|---|---|---|---|---|
| `qrng` | Verifiable randomness, 1–1024 bytes | `GET https://animica.dev/x402/qrng/draw` · `POST /x402/qrng` | 0.01 | available |
| `random_int` | Uniform integers, rejection sampling | `POST https://animica.dev/x402/random/int` | 0.01 | available |
| `random_shuffle` | Fisher–Yates permutation | `POST https://animica.dev/x402/random/shuffle` | 0.02 | available |
| `random_pick` | Weighted pick / lottery / sortition | `POST https://animica.dev/x402/random/pick` | 0.02 | available |
| `random_bulk` | 6–10 independent draws, one settlement | `POST https://animica.dev/x402/qrng/bulk` | 0.05 | available |
| `random_commit` | Commit–reveal (reveal is FREE) | `POST https://animica.dev/x402/random/commit` · free `GET /x402/random/reveal/{commit_id}` | 0.02 | available |
| `bulk_chain` | Block/tx range exports (NDJSON/JSON, gzip, cursor) | `GET https://animica.dev/x402/chain/export` · `/chain/blocks` · `/chain/transactions` | 0.05 | available |
| `chain_address_history` | Account history from a gateway-built index | `POST https://animica.dev/x402/chain/address-history` | 0.05 | available |
| `chain_batch_balances` | Balances for ≤500 accounts, head-pinned | `POST https://animica.dev/x402/chain/balances` | 0.02 | available |
| `priority_inference` | OpenAI-compatible priority chat completions | `POST https://animica.dev/x402/v1/chat/completions` | 0.10 | **unavailable** — `priority_inference_unavailable` (insufficient serving capacity) |

Caps worth quoting in listings: ≤1000 blocks or ≤10000 tx records (≤16 MB) per export;
≤500 addresses per balance batch; 1–1024 bytes per randomness draw. Oversized requests are
refused **before** any payment is taken.

Featured/primary product for any directory that allows only one:
**https://animica.dev/x402/qrng/draw** ($0.01).

## 6. Repositories and packages

| What | URL |
|---|---|
| Monorepo (canonical source, gateway under `apps/x402-gateway/`) | https://github.com/animicaorg/all |
| Core mirror | https://github.com/animicaorg/animica-core |
| MCP packaging/registry mirror | https://github.com/animicaorg/animica-mcp |
| PyPI — CLI/node/wallet | https://pypi.org/project/animica/ |
| PyPI — MCP wrapper | https://pypi.org/project/animica-mcp/ |
| Dependency-free JS verifier for the randomness rules | https://github.com/animicaorg/all/blob/main/randomness/beacon_api/static/verify.js |
| Gateway docs | `docs/x402.md` in the monorepo |

Note (2026-08-15): the monorepo's public `main` is behind local work — `docs/x402.md` and
`apps/x402-gateway/` are not visible on github.com until the next push. Verify a blob URL
returns 200 before pasting it into a submission form (`verify.js` above is confirmed 200).

## 7. MCP cross-discovery

```
pip install animica-mcp      # or: uvx animica-mcp
```

| Registry | Server name |
|---|---|
| MCP official registry (publisher namespace) | `org.animica/animica` |
| MCP official registry (GitHub namespace) | `io.github.animicaorg/animica` |

Both are `active` in `https://registry.modelcontextprotocol.io/v0/servers?search=animica`
(verified 2026-08-15). The server is read-only and holds no keys; the x402 catalog tool
`animica_x402_products` reads `/.well-known/x402` and reports what exists, what it costs
and whether it is available — it never signs or settles a payment. Paying requires an
x402-capable HTTP client.

**Release lag to check before quoting the MCP tool in a listing (2026-08-15):**
`animica_x402_products` is committed in the monorepo but is **not** in the published
`animica` 10.1.0 wheel (uploaded 2026-08-13) that `animica-mcp` 0.2.0 pulls in, so a user
installing today still gets the previous 15-tool set. Do not write "16 tools" or name the
x402 tool in an MCP listing until a release containing it is on PyPI; verify with
`animica mcp serve` (or `python -c "from animica.mcp.tools import TOOLS_BY_NAME; print(len(TOOLS_BY_NAME))"`)
against a clean install first.

## 8. x402scan — registration and refresh

**Current state (2026-08-15):** the resource registered at x402scan is
`c9a2a915-2a9d-42c4-9201-6f014258ae0f` → `https://animica.dev/x402/paid/echo`. That route is
the **development-only settlement smoke test** and is disabled in production
(`X402_ENV=production`, no `X402_ENABLE_ECHO`). It must not remain the public face of the
listing.

**A real Base-mainnet settlement now exists**, which is what x402scan's indexing keys off:

| Field | Value |
|---|---|
| Resource | `https://animica.dev/x402/qrng/draw` |
| Settlement tx | `0x3433107eaf69aad2fc6c3413a8a142ede18ad75859f5734ab1635d82f7069463` |
| Base block | 49,991,284 — receipt `status: 0x1` (verified against Base mainnet 2026-08-15) |
| Transfer | 10000 USDC atomic units ($0.01) to `0x20fEee2dC0d4b36f69ddca69d0cE32d7E80b27a6` |
| Settled at | 2026-08-15T05:32:00Z |
| Gateway record | `payments.payment_id = pay_mstxv3y4a72a346542290`, `status = settled` |

**Refresh steps (do these in order):**

1. Confirm the health check passes: `node scripts/check-x402-discovery.mjs` (exit 0).
2. Register/refresh the discovery URL **https://animica.dev/.well-known/x402** so the
   indexer enumerates every product with live prices and availability.
3. Set the **featured resource to `https://animica.dev/x402/qrng/draw`** (the QRNG draw is
   the differentiated listing), with the description:
   > Animica provides x402-paid machine APIs on Base, including verifiable quantum
   > randomness, bulk post-quantum L1 chain data, and priority AI inference.
   (Pair it with the trust-model sentence from §3 wherever the form allows a second field.)
4. **Retire / replace resource `c9a2a915-2a9d-42c4-9201-6f014258ae0f`** (`/x402/paid/echo`).
   If the directory cannot delete a resource, point the listing at the QRNG URL and mark
   echo as development-only so no agent tries to buy it.
5. Re-check that the listing shows `priority_inference` as unavailable (or omits it) —
   never as a purchasable product while capacity is below the floor.

Other neutral aggregators to submit the same discovery URL to (no Coinbase properties):
x402-list.com, agent-tools.cloud, and any indexer that reads `/.well-known/x402` or the
open bazaar-format metadata in 402 responses. No mass/spam submissions.

## 9. Submission checklist (paste-time)

- [ ] `/x402` and `/.well-known/x402` return 200 and the catalog matches the prices used.
- [ ] Every product URL quoted resolves (402 unpaid is the expected answer for paid routes).
- [ ] Randomness copy says **verifiable** and states `is_quantum:false` / `attested:false`;
      no hardware-QRNG or hardware-attestation claim anywhere.
- [ ] Priority inference is listed as unavailable, or not listed.
- [ ] `/x402/paid/echo` appears nowhere.
- [ ] "Public/free APIs remain free" appears wherever chain data is described.
- [ ] No Coinbase service, account, API, SDK or URL anywhere in the submission.
- [ ] Prices copied from the live catalog, not from this file's snapshot.
