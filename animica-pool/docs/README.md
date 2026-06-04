# Animica Pool — Documentation

Guides (expanded across phases):

- What is Animica Pool?
- How to mine ANM / XMR / dual (P4)
- How ANM payouts work (P4/P8)
- How to buy AI credits (P3)
- Using the OpenAI-compatible API (P2)
- Creating API keys (P1) ✅
- Connecting a worker (P5)
- How compute rental works (P6)
- How payouts work (P8)
- Provider routing & Bittensor integration (P2/P7)
- NOWPayments setup (P3/P8)
- Admin deployment guide

## OpenAI-compatible API (preview)

```ts
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "anm_live_xxx",            // create at /api-keys
  baseURL: "https://pool.animica.org/v1",
});

const response = await client.chat.completions.create({
  model: "anm-fast-8b",
  messages: [{ role: "user", content: "Explain Animica Pool" }],
});

console.log(response.choices[0].message.content);
```

Models: `anm-fast-8b`, `anm-code-7b`, `anm-pro-70b`, `anm-bittensor-router`, `anm-embed`, `anm-worker-small`, `anm-worker-code`.
