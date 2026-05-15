---
title: "Send your first transaction"
summary: "Send ANIMICA from your wallet to a friend's address and confirm the transfer on the explorer."
track: "wallet"
difficulty: "intro"
order: 2
estimatedMinutes: 4
rewardAnim: "1.5"
steps: 3
stepIds: ["compose", "broadcast", "explorer-check"]
---

import Step from "../../components/Step.astro";

<Step id="compose" title="Compose a transfer">

Open the wallet extension. Click **Send**, paste a destination `anim1...` address (the academy demo address below is safe to use), and enter `0.1` as the amount.

```
anim1demo000academy000practice0000000000
```

The extension shows the destination, amount, and the fee it computed. Read every field before continuing.

Mark this step complete after the confirmation screen appears in the extension.

</Step>

<Step id="broadcast" title="Sign and broadcast">

Click **Confirm** in the extension. The transaction is signed offline (your seed never leaves the extension process) and broadcast to the node configured in your wallet settings.

The extension shows a "broadcast" toast with a tx hash. Click it to copy the hash — you'll need it in the next step.

Mark this step complete after the extension reports a broadcast tx hash.

</Step>

<Step id="explorer-check" title="Verify on the explorer">

Open the explorer in a new tab:

```
https://explorer.animica.org
```

Paste the tx hash into the search bar. You should see a transaction with your address as `from`, the demo address as `to`, and an `included` block height.

If you see "pending", wait a few seconds and refresh — once a block including your tx is mined, the status flips to `included`.

Mark this step complete once you've found your transaction on the explorer.

</Step>
