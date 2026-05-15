---
title: "Become an AICF compute provider"
summary: "Detect your hardware, register as an AICF worker, and serve your first inference job for an animica chat user."
track: "aicf"
difficulty: "intermediate"
order: 2
estimatedMinutes: 12
rewardAnim: "5"
steps: 4
stepIds: ["hardware", "register", "first-job", "verify-earning"]
---

import Step from "../../components/Step.astro";

<Step id="hardware" title="Detect your hardware">

The animica CLI ships a hardware detector that tells you which AICF model tiers you can serve:

```
animica miner aicf-worker hardware
```

The output is a JSON object with `cpu_cores_logical`, `ram_gb`, `gpus`, `accelerator_preferred`, and `eligible_tiers`. A laptop CPU is usually eligible for `tiny`; a discrete GPU with ≥ 8 GB VRAM unlocks `small`; ≥ 24 GB unlocks `flagship`.

Mark this step complete after the detector prints your eligible tiers.

</Step>

<Step id="register" title="Register the worker">

Register with the pool, advertising your tiers:

```
animica miner aicf-worker register --address <your-anim1-address>
```

The registration writes a worker entry into the AICF state on-chain: address, advertised tiers, hardware fingerprint. It is idempotent — re-running just refreshes the fingerprint.

Mark this step complete after you see "registered as worker <…>".

</Step>

<Step id="first-job" title="Serve your first job">

Start the worker loop:

```
animica miner aicf-worker start --address <your-anim1-address>
```

The loop polls for jobs that match your advertised tiers and streams replies as it generates them. On a small GPU you'll typically see your first job within 30–60 seconds during busy periods.

Mark this step complete after the worker reports `served job 0x…` at least once.

</Step>

<Step id="verify-earning" title="Verify your earning">

Each completed job emits a settlement transaction. Open the explorer:

```
https://explorer.animica.org/address/<your-anim1-address>
```

You should see incoming transfers tagged with the AICF settlement memo. Sum them: that's what the network has paid you so far.

Mark this step complete after you've verified at least one inbound settlement.

</Step>
