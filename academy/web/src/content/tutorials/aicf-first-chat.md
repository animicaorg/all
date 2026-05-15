---
title: "Run your first AICF chat"
summary: "Install the animica CLI, run `animica chat`, send a prompt that's served by a real distributed worker, settle the credit on-chain."
track: "aicf"
difficulty: "intro"
order: 1
estimatedMinutes: 8
rewardAnim: "2"
steps: 4
stepIds: ["install-cli", "configure-wallet", "first-prompt", "settle"]
---

import Step from "../../components/Step.astro";

<Step id="install-cli" title="Install the animica CLI">

The Python CLI ships the chain client, the wallet importer, and the AICF chat client in a single wheel:

```
pip install animica
```

Verify the install:

```
animica --version
```

You should see `animica 0.1.x`. Mark this step complete after the version prints.

</Step>

<Step id="configure-wallet" title="Configure the wallet for chat">

Point the chat client at the wallet you funded in the wallet track:

```
animica wallet use <your-anim1-address>
animica chat config --rpc https://rpc.animica.org
```

The first command tells the client which wallet to debit for AICF jobs; the second points it at the public RPC. Both write to `~/.animica/config.yaml`.

Mark this step complete after both commands return success.

</Step>

<Step id="first-prompt" title="Send your first prompt">

Start the REPL:

```
animica chat
```

You should see a `›` prompt. Type:

```
hello — are you running on a distributed worker?
```

The client:

1. Asks the AICF RPC to estimate the cost.
2. Reserves that amount from your wallet (extension confirmation if interactive).
3. Streams the reply token-by-token as the worker emits them.

Mark this step complete after you've received a streamed reply.

</Step>

<Step id="settle" title="Settle the job on-chain">

After the stream ends, the client prints a one-line job summary:

```
[job 0xab12…] 240 tokens · settled in 0x9ce1… (block 18234)
```

The `settled` hash is the on-chain settlement transaction — it credits the worker who served you and finalizes the debit on your wallet. Paste that hash into the explorer to verify.

Mark this step complete after you've located the settlement tx on the explorer.

</Step>
