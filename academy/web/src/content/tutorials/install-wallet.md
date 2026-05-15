---
title: "Install the Animica wallet"
summary: "Install the browser-extension wallet, create a fresh wallet, back up the seed, fund it from the faucet."
track: "wallet"
difficulty: "intro"
order: 1
estimatedMinutes: 5
rewardAnim: "1"
steps: 4
stepIds: ["install", "seed-backup", "faucet", "balance-check"]
---

import Step from "../../components/Step.astro";

<Step id="install" title="Install the extension">

Open the Animica wallet download page and install the extension for your browser:

```
https://animica.org/wallet
```

After install, the puzzle-piece icon in your browser toolbar shows the Animica logo. Click it once to open the extension.

Mark this step complete after the extension popup opens for the first time.

</Step>

<Step id="seed-backup" title="Create a wallet and back up the seed">

In the extension popup, click **Create new wallet**. The extension generates a 24-word recovery seed offline — you never type or paste it into any website.

Write the seed down on paper (not in a screenshot, not in a password manager that syncs to the cloud, **paper**). The seed is the only way to recover the wallet — Animica engineering can't help you if you lose it.

Confirm the seed by re-entering it in the order the extension asks for, then set a local password.

Mark this step complete once your wallet is created and you've stored the seed offline.

</Step>

<Step id="faucet" title="Fund from the academy faucet">

Open the academy faucet:

```
https://animica.org/faucet
```

Paste your `anim1...` address, prove you're human, and submit. The faucet sends a small amount of ANIMICA — enough to cover fees for the rest of this track.

Mark this step complete after the faucet confirms a tx hash for the transfer.

</Step>

<Step id="balance-check" title="See your balance in the extension">

Open the wallet extension again. The balance widget should now show a non-zero ANIMICA balance — usually within 15-30 seconds of the faucet's confirmation.

If the balance is still zero after a minute, click the refresh icon in the extension header; it triggers an explicit re-fetch from the configured RPC.

Mark this step complete after you see the funded balance. Then click **Claim reward** in the side panel — the extension will ask you to sign a small attestation, and the reward pool will send your reward.

</Step>
