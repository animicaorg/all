# @animica/sdk

Official JavaScript/TypeScript SDK for the **Animica** post-quantum L1 blockchain (coin: **ANM**).

- **Zero runtime dependencies** — built on the native `fetch` in Node ≥ 18 (works in browsers, Deno, Bun, and edge runtimes too).
- **ESM + CJS** dual output with full TypeScript types.
- Modules: node **JSON-RPC**, free **OpenAI-compatible AI** API, **bech32m address** codec, **price** feed, **chain stats**.

```bash
npm install @animica/sdk
```

Requires Node **>= 18**.

## Quickstart

### RPC — talk to a node

```ts
import { JsonRpcClient } from "@animica/sdk";

const rpc = new JsonRpcClient(); // defaults to https://rpc.animica.org/rpc
// const rpc = new JsonRpcClient("http://localhost:8545"); // your own node

const head = await rpc.getHead();
console.log(head.height, head.thetaMicro); // difficulty = Θ in micro-nats

const params = await rpc.getParams();         // symbol ANM, decimals 9, ...
const supply = await rpc.getTotalSupply();    // { totalSupply: "0x...", addressCount }
const bal = await rpc.getBalance("anim1...");// bigint, base units (nANM, 1e-9 ANM)

const block = await rpc.getBlockByHeight(63000);
const same = await rpc.getBlockByHash(block.hash);

const mempool = await rpc.getMempoolStats(); // { count, totalBytes, oldestAgeSec }

// Submit a signed CBOR tx (0x-hex). The SDK treats it as opaque bytes:
const txHash = await rpc.sendRawTransaction("0x...signed-cbor...");

// Anything else on the node (see `rpc.discover` for the full surface):
const forks = await rpc.call("chain.getForks");
```

Errors: JSON-RPC errors throw `RpcError` (`.code`, `.data`, `.method`); transport
failures throw `RpcTransportError` (`.status`).

### AI — free OpenAI-compatible API

[animica.dev](https://animica.dev) exposes a keyless, rate-limited OpenAI-compatible
`/v1`. No API key is required (one is accepted and sent as a Bearer token if you have one).

```ts
import { AnimicaAI } from "@animica/sdk";

const ai = new AnimicaAI(); // https://animica.dev/v1

const models = await ai.models.list(); // includes "kimi-k3"

const chat = await ai.chat.completions.create({
  model: "kimi-k3",
  messages: [{ role: "user", content: "Explain PoIES in one sentence." }],
});
console.log(chat.choices[0].message.content);

// Streaming (SSE, parsed with native fetch):
const stream = await ai.chat.completions.create({
  model: "kimi-k3",
  messages: [{ role: "user", content: "Write a haiku about SHA3." }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}
```

Note: models are served by the Animica miner network — a model can be listed
with `serving: false` when no miner currently serves it.

### Addresses — bech32m `anim1…`

Self-contained bech32m (BIP-350) codec matching the chain's canonical Python
implementation. Payload = 2-byte algorithm id (big-endian) + 32-byte
SHA3-256(pubkey) digest; the live signature scheme is ML-DSA-65 (`0x1003`).

```ts
import {
  validateAddress,
  decodeAddress,
  encodeAddress,
  shortAddress,
  ALG_ID_ML_DSA_65,
} from "@animica/sdk";

validateAddress("anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga"); // true
validateAddress("anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946gb"); // false (bad checksum)

const rec = decodeAddress("anim1..."); // { hrp, algId, digest, payload } — throws AddressError on bad input
rec.algId === ALG_ID_ML_DSA_65;

const addr = encodeAddress(payload34Bytes); // Uint8Array -> "anim1..."
shortAddress(addr);                          // "anim1z…r946ga"
```

### Price — ANM/USDT

```ts
import { fetchPrice } from "@animica/sdk";

const p = await fetchPrice(); // https://animica.org/anm-price.json
console.log(p.last, p.base_volume, p.market_url); // from nonkyc.io
```

### Stats — chain stats

```ts
import { fetchStats } from "@animica/sdk";

const s = await fetchStats(); // https://animica.org/api/stats
console.log(s.height, s.difficulty, s.network_hashrate_hs); // difficulty = Θ in micro-nats
console.log(s.block_reward, s.block_reward_breakdown?.miner, s.avg_block_time_1h_s);
console.log(s.price_usd, s.supply?.circulating_anm, s.pools?.[0]?.stratum);
```

The public `https://animica.org/api/stats` URL goes live with the animica.org
deploy; until then — or to point at your own mirror — pass an override URL:
`fetchStats({ url: "http://127.0.0.1:8560/api/stats" })`. The document is typed
as `ChainStats`, mirroring the API's snake_case fields exactly; every field is
optional and unknown fields are preserved.

## Payments & signing

**Transaction signing is out of scope for v0.1.** Animica uses post-quantum
ML-DSA-65 signatures over canonical CBOR signing preimages; `sendRawTransaction`
only relays bytes you already signed. To create and sign transactions use the
Animica wallets (browser extension, mobile, desktop) or the Python SDK/CLI —
see <https://animica.org/wallet> and the docs in
[animicaorg/all](https://github.com/animicaorg/all).

## Advanced

Every client accepts `{ fetch, timeoutMs, headers }` for custom transports,
proxies, and testing:

```ts
const rpc = new JsonRpcClient({ url: "http://localhost:8545", timeoutMs: 5000 });
const ai = new AnimicaAI({ apiKey: process.env.ANIMICA_KEY, fetch: myFetch });
```

## Links

- Chain + explorer: <https://animica.org> · <https://explorer.animica.org>
- Public RPC: <https://rpc.animica.org/rpc> (POST JSON-RPC 2.0; `rpc.discover` lists all methods)
- Free AI API: <https://animica.dev> (OpenAI-compatible `/v1`)
- Mining pool: <https://pool.animica.org>
- Source: <https://github.com/animicaorg/all> (`packages/animica-sdk`)

## License

MIT
