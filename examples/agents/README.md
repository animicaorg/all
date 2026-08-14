# Animica agent demos

Small, self-contained Python scripts showing how an AI agent (or any program)
can use the Animica network. Each is standard library only unless noted, under
120 lines, and safe to run: read-only chain access, no keys, no spending.

All demos were run against mainnet on 2026-08-14 — the output snippets below
are real.

| Demo | What it shows | Run |
|---|---|---|
| `chain_info.py` | JSON-RPC head + total supply (stdlib only) | `python3 chain_info.py` |
| `check_tx.py` | Tx lookup: explorer REST with RPC fallback | `python3 check_tx.py 0x<hash>` |
| `free_inference.py` | Free keyless OpenAI-compatible AI at animica.dev | `python3 free_inference.py "prompt"` |
| `l2_status.py` | ANM-native L2 rollup status + TPS counters | `python3 l2_status.py` |
| `mcp_demo.py` | Driving the Animica MCP server over stdio | `python3 mcp_demo.py` |
| `deploy_preview.py` | Animica Deploy's free site-preview API | `python3 deploy_preview.py https://your-site.example` |

## Install

Nothing is required for the stdlib demos (`chain_info.py`, `check_tx.py`,
`l2_status.py`, `deploy_preview.py`; `free_inference.py` also works
stdlib-only). For the full set:

```bash
pip install animica mcp    # animica = node/wallet/CLI + MCP server; mcp = client SDK
pip install openai         # optional: free_inference.py uses it when present
```

Python 3.10+.

## Endpoints used

- Node JSON-RPC: `POST https://rpc.animica.org/rpc` (JSON-RPC 2.0; always use
  the `/rpc` path — the bare domain root 301-redirects, which breaks naive
  POST clients)
- Explorer REST: `https://explorer.animica.org/api/…` (free, no auth)
- Free AI: `https://animica.dev/v1` (OpenAI-compatible, keyless, 30 req/min/IP)
- Deploy preview: `POST https://animica.org/deploy/api/preview` (free, JSON)

More: [developer docs](https://animica.org/developers/) ·
[monorepo](https://github.com/animicaorg/all) ·
[PyPI `animica`](https://pypi.org/project/animica/)

## Expected output

### chain_info.py

```
Animica L1 (chain id 1)
  height        : 73192
  block hash    : 0x00000000009712b987635ad5d692d15655c35ede8bf441dbf74c780410edd145
  theta (micro) : 26879348
  nonce         : 15587208154
  total supply  : 108,385,132.60 ANM (108385132601017869 base units)
  addresses     : 146
```

### check_tx.py

Grab any recent hash from `https://explorer.animica.org/api/blocks` first.

```
$ python3 check_tx.py 0x4a35ffed4f8b747ad533b9a72303d9d7212ee2fb0daa00084171976c9a4ba0a7
tx      : 0x4a35ffed4f8b747ad533b9a72303d9d7212ee2fb0daa00084171976c9a4ba0a7
status  : confirmed  (confirmations: 2)
block   : height 73191  0x00000000013b4e8b8a60f3b4185a4cd5415e487562ca346931324be95b8cab56
from    : anim1zqp6zhtjsvse38vlkekdh7d44k2rmvctvkc8n6m55dhh5n8x6rpqavgztza8r
to      : anim1zqpe6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcqsu3y9
value   : 0.000002000 ANM (2000 base units)
type    : native_transfer  failed=False
```

Unknown hashes print `Transaction 0x… not found` and exit 0. If the explorer
is down the script falls back to node RPC `tx.getStatus` automatically.

### free_inference.py

Capacity is community-GPU-provided: each model in `/v1/models` has a boolean
`serving` flag, and the script only sends a completion when one is true. At
the time of writing none was serving, so the honest output is:

```
models: kimi-k3(down), animica-chat(down), animica-chat-small(down), animica-chat-flagship(down), animica-knowledge(down)
No model is currently serving (all `serving` flags are false in https://animica.dev/v1/models). Capacity on animica.dev is provided by community GPU workers, so it comes and goes — retry later, or run a worker yourself: pip install animica && animica up
```

When a worker is online the script asks the first serving model your prompt
and prints the answer.

### l2_status.py

```
Animica L2 rollup
  enabled      : True  (mode: all)
  l2 chain id  : 1001
  settlement   : VALIDITY
  head batch   : 2  (pending txs: 0)
  state root   : 0x7012d7a04128512db6bfc7ef49154a6fe6a095c1b6f62ee6de2b601dcc41c1ca
  bridge addr  : anim1zqpm5cstcss6ct8jwtguefsfh7ufga2y8r6lcsxrq2yz6ek05gt0yfqrnye8q
  locked on L1 : 1,000.35 ANM (deposits: 3, withdrawals: 0)
throughput counters
  executed total : 1  (0.0 tps)
  soft-confirmed : 1  (0.0 tps)
  settled total  : 0  (0.0 tps)
  batches total  : 1
```

### mcp_demo.py

Spawns `python -m animica.mcp.server` (stdio) via the `mcp` client SDK,
lists its 15 read+compute tools, then calls `animica_info` and
`animica_chain_head`:

```
tools (15):
  - animica_info: What Animica is and how to use it: the OpenAI-compatible AI API, the
  - animica_ai_ask: Ask Animica's ENA AI a question (OpenAI-compatible inference). Cheap general
  ...
  - animica_chain_head: Read the chain head: current height/hash and chain id. Read-only.
  ...

=== animica_info ===
{ "summary": "Animica is an open AI + blockchain network. ...", ... }

=== animica_chain_head ===
{ "head": { "height": 73192, "chainId": 1, ... }, "chain_id": 1 }
```

(The server also logs request lines to stderr — normal.) Running from a
source checkout instead of pip: `PYTHONPATH=/path/to/animica/python python3
mcp_demo.py`.

### deploy_preview.py

One free JSON call (rate-limited per IP; the server crawls up to 5 pages of
your site, so allow ~25 s). No payment is involved — checkout is a separate
flow this script never touches.

```
$ python3 deploy_preview.py https://example.com
POST https://animica.org/deploy/api/preview
     {"url": "https://example.com"}  (crawling target — may take ~25s)

preview result
  site name  : Example Domain
  origin     : https://example.com
  pages seen : 1
  topics     : (none detected)
  page       : 'Example Domain' — https://example.com/
  normalized : https://example.com/
```

## Conventions worth knowing

- Amounts are base units: 1 ANM = 10^9 base units.
- Addresses are bech32m, prefix `anim1…`.
- Signatures are ML-DSA-65 (FIPS 204), scheme id 0x1003 (not used by these
  read-only demos).
- `tx.getStatus` takes the hash as a positional param:
  `{"method":"tx.getStatus","params":["0x…"]}`.
