---
title: "Run a local node"
summary: "Bring up an animica node with `animica node up`, watch the head height climb, query the local RPC."
track: "node"
difficulty: "intro"
order: 1
estimatedMinutes: 8
rewardAnim: "2"
steps: 3
stepIds: ["node-up", "head-sync", "rpc-query"]
---

import Step from "../../components/Step.astro";

<Step id="node-up" title="Bring up the node">

Install the CLI (if you haven't already) and start the standalone compose stack:

```
pip install animica
animica node up
```

The CLI uses the bundled `image:`-based compose file that ships in the wheel — no source checkout required. Docker pulls the configured `animica/node` image and starts a single-node devnet by default.

Mark this step complete after `docker ps` shows the `animica-node` container in the `Up` state.

</Step>

<Step id="head-sync" title="Watch the head height climb">

Tail the node's structured logs:

```
animica node logs --follow
```

You should see new-block lines every few seconds:

```
new-block height=12 txs=0 hash=0x…
new-block height=13 txs=0 hash=0x…
```

The chain mines empty blocks at idle. Send a tx to it (from the wallet track) to see one with a non-zero `txs` count.

Mark this step complete after you've seen at least three `new-block` lines.

</Step>

<Step id="rpc-query" title="Query the local RPC">

The node exposes the JSON-RPC server on `http://127.0.0.1:8545` by default. Ask it for the current head:

```
curl -X POST -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"animica.head","params":[]}' \
  http://127.0.0.1:8545
```

You should get a JSON response with the current head height and hash matching the latest log line.

Mark this step complete after the curl call returns a successful response.

</Step>
