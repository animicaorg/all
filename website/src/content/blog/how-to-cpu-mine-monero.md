---
title: "How to CPU-mine Monero (XMR) in 2026 — a beginner's guide"
description: "Monero is one of the only major coins you can still profitably mine on a normal CPU. Here's how RandomX works, what you need, and how to start in one command."
date: 2026-06-04
author: "Animica Team"
tags: ["monero", "cpu-mining", "randomx", "tutorial"]
---

Most cryptocurrencies stopped being mineable on ordinary computers years ago — ASICs and GPU farms took over. **Monero (XMR) is the big exception.** Its RandomX proof-of-work is deliberately designed to run best on general-purpose CPUs, which keeps mining open to anyone with a laptop or desktop.

This guide explains what you actually need, sets honest expectations, and gets you mining in a single command.

## Why Monero is CPU-friendly

Monero uses an algorithm called **RandomX**. It's tuned for the kind of fast caches and general-purpose instructions that CPUs have and ASICs don't, so a custom mining chip has little advantage. In practice that means:

- A modern multi-core CPU is competitive hardware.
- A GPU is *not* meaningfully better for RandomX (often worse per watt).
- You don't need special equipment to start — just the computer you already own.

## What you need

1. **A 64-bit CPU** (more cores = more hashes). Ryzen chips with large L3 cache do especially well.
2. **~2.5 GB of RAM free per mining thread** for RandomX's dataset (fast mode). Less RAM still works in light mode, slower.
3. **Python 3.10+** to run the one-command miner below.
4. **A payout address.**

## Set honest expectations first

CPU mining will not make you rich, and anyone promising fixed returns is lying. Your earnings depend on your CPU's hashrate, Monero's network difficulty, the XMR price, and your electricity cost. A typical desktop produces a few thousand H/s; a strong many-core machine, tens of thousands. Run the numbers on your own power cost before scaling up. Think of it as participating in and securing a network — sometimes profitably — not a money printer.

## The fast way: one command

The [Animica](https://animica.org) pool lets you mine on your CPU and — uniquely — **mine Monero and Animica (ANM) at the same time** from a single command, splitting your CPU threads between the two. You can also mine pure Monero if you prefer.

Install the CLI (Python 3.10+):

```bash
python3 -m venv ~/animica-venv
source ~/animica-venv/bin/activate     # Windows: animica-venv\Scripts\activate
pip install --upgrade "animica>=0.3.13"
```

Then start mining. For **pure Monero**, get paid in XMR:

```bash
animica miner dual-mine <your-anim1-address> --only xmr \
  --pool-host pool.animica.org --threads 8 --worker myrig
```

Or **dual-mine** ANM + Monero 50/50 (best total yield) by dropping `--only xmr`:

```bash
animica miner dual-mine <your-anim1-address> \
  --pool-host pool.animica.org --threads 8 --worker myrig
```

The CLI auto-downloads the right miner binary for Windows, macOS, or Linux — no manual setup. Your shares, hashrate, and balance show up under your address on the [pool stats page](https://pool.animica.org/stats).

## Choosing your thread count

Start with `--threads` set to about the number of physical cores you can spare (leave 1–2 for the OS). More threads = more hashrate but a hotter, busier machine. If you want to keep using the computer, mine with half your cores.

## Where to go next

- [Set up mining on the Animica pool](https://pool.animica.org/mine) — pick pure ANM, pure XMR, or dual.
- [Dual-mine XMR + ANM with one command](/blog/dual-mining-one-command) — put your spare threads to work.
- [Run a full node](https://academy.animica.org/tutorials/run-a-node) — for the fully self-reliant setup.

Mining is the most hands-on way to learn how a blockchain actually works. Start small, watch your shares land, and scale from there.
