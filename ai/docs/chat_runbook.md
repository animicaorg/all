# `animica chat` runbook

Interactive AICF-paid REPL for the Animica coding agent. Spends ANIMICA from
your configured wallet, routes prompts to miners running the flagship model,
streams responses back.

## Prerequisites

- Animica python package installed (`pip install animica` or running from
  source tree).
- A configured wallet under `~/.animica/wallets/` (or `$ANIMICA_DATA_DIR`).
- For paid mode: wallet funded with ANIMICA.
- For offline-fallback mode: an exported local-flagship bundle at
  `ai/flagship_agent/models/export/latest`.

## Quick start

```bash
# 1. mint a wallet if you don't already have one.
animica wallet new

# 2. set the active network if it isn't already.
animica network set mainnet

# 3. open the chat.
animica chat
```

The REPL prints a banner showing the AICF endpoint and provider cascade
status, then waits for input.

## Slash commands

| Command | What it does |
|---|---|
| `/help` | List all slash commands. |
| `/quit` | Exit cleanly. |
| `/balance` | Refresh and print wallet balance + provider statuses. |
| `/history` | List the turns in this session. |
| `/save` | Persist the transcript under `~/.animica/agent_runtime/history/`. |
| `/provider <name>` | Force `distributed-aicf`, `local-flagship`, or `offline` for the next turn. |
| `/tier <id>` | Prefer a specific model tier (`tiny`|`small`|`flagship`|`large`). |
| `/clear` | Clear the screen and forget conversation history. |
| `/status` | Show full provider cascade status. |

## Flags

```bash
animica chat \
  --rpc-url http://my-node:8545/rpc \
  --wallet ~/.animica/wallets/dev.json \
  --yolo \
  --require-distributed
```

- `--yolo` — skip per-turn cost confirmation.
- `--require-distributed` — refuse to fall back to `local-flagship` or
  `offline` if `distributed-aicf` is unavailable.

## Cost preview

By default the REPL prints a per-turn cost estimate before submitting the
job and asks `proceed? [Y/n]`. To skip the prompt, pass `--yolo` or set
`ANIMICA_CHAT_YOLO=1`.

If your wallet balance can't cover the estimate (plus a small reserve for
chain fees), the provider refuses and the cascade falls through to the next
provider (`local-flagship`, then `offline`) unless `--require-distributed`
is set.

## Provider cascade

```
distributed-aicf  → miners run flagship, paid in ANIMICA       (default)
local-flagship    → local bundle, no payment                   (fallback)
offline           → static templates, no payment, no inference (last resort)
```

Each turn's footer shows which provider answered:

```
  provider=distributed-aicf  tier=flagship  cost=0.000123 ANIMICA  latency=842ms
```

If you suddenly see shorter / lower-quality answers, check `/status` —
you may have silently fallen back to `local-flagship` because your wallet
ran dry. Top up and `/provider distributed-aicf` to switch back.

## Honesty guarantees

Every turn's metadata carries `requested_tier`, `tier`, `effective_mode`,
`fallback_reasons`. The local bundle is only used if its `manifest.json`
declares `available_for_real_inference: true` — bundles built in
`simulate` or `lite` mode never serve real chat.
