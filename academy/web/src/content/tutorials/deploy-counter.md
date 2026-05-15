---
title: "Deploy your first contract"
summary: "Deploy the counter contract from animica/stdlib, call it from the wallet, and read the new state on the explorer."
track: "contracts"
difficulty: "intermediate"
order: 1
estimatedMinutes: 10
rewardAnim: "3"
steps: 4
stepIds: ["compile", "deploy", "call-increment", "read-state"]
---

import Step from "../../components/Step.astro";

<Step id="compile" title="Compile the counter">

The stdlib counter is shipped with the animica wheel. Print its compiled bytecode to your terminal:

```
animica contract compile --from-stdlib counter
```

You should see a `0x...` bytecode blob followed by the ABI. Save the ABI to a file for the deploy step.

Mark this step complete after the compile prints successfully.

</Step>

<Step id="deploy" title="Deploy the bytecode">

Deploy to the configured network, paying gas from your wallet:

```
animica contract deploy --bytecode <paste-from-step-1> --from <your-anim1-address>
```

The CLI signs the deploy via the configured wallet provider (the extension if it's running). Confirm in the extension when prompted.

The result is a contract address — copy it for the next step.

Mark this step complete after a contract address is printed.

</Step>

<Step id="call-increment" title="Call increment()">

Call the counter's `increment()` method:

```
animica contract call <contract-address> increment --from <your-anim1-address>
```

Confirm the call in the wallet extension. The CLI prints the included tx hash on success.

Mark this step complete after `increment` returns a successful tx hash.

</Step>

<Step id="read-state" title="Read the counter">

Read the current `value()`:

```
animica contract call <contract-address> value --read-only
```

The `--read-only` flag uses an `eth_call`-style local call; no transaction is broadcast. The printed value should be `1` (or higher, if you called `increment` more than once).

You can also paste the contract address into the explorer:

```
https://explorer.animica.org/contract/<contract-address>
```

The contract detail page shows the verified ABI (if uploaded), recent calls, and storage values.

Mark this step complete after you see `value == 1` (or higher) returned.

</Step>
