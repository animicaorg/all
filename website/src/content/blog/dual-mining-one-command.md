---
title: "Dual-mine two coins with one command: Animica (ANM) + Monero (XMR)"
description: "Most miners run one coin per machine. Animica's CLI mines ANM (SHA3) and Monero (RandomX) at the same time on your CPU, 50/50, from a single command."
date: 2026-06-04
author: "Animica Team"
tags: ["dual-mining", "monero", "animica", "cpu-mining", "tutorial"]
---

Running a separate miner for each coin is fiddly: two configs, two processes, two things to babysit. Animica collapses that into **one command that mines two coins at once** on the same CPU.

## How dual mining works here

The `animica` CLI launches two CPU miners side by side and splits your threads between them:

- **Animica (ANM)** on the SHA3 algorithm.
- **Monero (XMR)** on RandomX.

By default it's a 50/50 thread split, so half your cores work each chain. You earn shares on both simultaneously, and you can take payouts in ANM or XMR.

## Start in one command

Install the CLI (Python 3.10+):

```bash
python3 -m venv ~/animica-venv
source ~/animica-venv/bin/activate     # Windows: animica-venv\Scripts\activate
pip install --upgrade "animica>=0.3.13"
```

Then dual-mine:

```bash
animica miner dual-mine <your-anim1-address> \
  --pool-host pool.animica.org --threads 8 --worker myrig
```

That's it. The CLI auto-downloads the correct miner binaries for your OS. Prefer a single coin? Add `--only animica` or `--only xmr`.

## Why dual-mine instead of one coin?

- **Better total yield from idle cores.** If one chain's difficulty spikes or price dips, the other keeps contributing.
- **One workflow.** One command, one process tree, one worker name across both pools.
- **Optional ANM payouts.** Earn a stake in a newer network while still mining established XMR.

Honest note: dual mining splits your hashrate, so you won't get full-speed output on *either* coin — you get a blend. Whether that beats mining a single coin depends on prices and difficulty at the moment. It's easy to switch with `--only`.

## Tuning

- `--threads` — total cores across both algos (split 50/50). Leave 1–2 for your OS.
- `--worker` — a label so you can tell your machines apart in [stats](https://pool.animica.org/stats).
- `--only animica|xmr` — run a single chain instead of dual.

## Next steps

- [Mining setup on the pool](https://pool.animica.org/mine)
- [How to CPU-mine Monero](/blog/how-to-cpu-mine-monero)
- [Live pool & network stats](https://pool.animica.org/stats)
