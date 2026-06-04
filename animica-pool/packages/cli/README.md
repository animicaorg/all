# @animica/cli — Animica Pool CLI

Mine, rent compute, serve/consume AI inference, and manage keys/credits/payouts from the terminal.

## Install
```bash
# one-liner (no npm needed)
curl -fsSL https://pool.animica.org/install-cli.sh | bash
# or via npm
npm i -g @animica/cli
```

## Use
```bash
animica-pool register --email you@x.com --password ****
animica-pool login    --email you@x.com --password ****

# Mine — prints the exact `animica miner …` connection command
animica-pool mine --mode dual --address anim1… --xmr 4… --payout ANM --worker rig-01
animica-pool mine-stats

# AI
animica-pool keys create --label myapp
animica-pool credits buy --amount 50
animica-pool infer --key anm_live_… --model anm-fast-8b --prompt "Hello"

# Workers / rentals / payouts
animica-pool worker create --name gpu-box
animica-pool rent list --type gpu
animica-pool rent order --rig <id> --hours 2 --pay credits
animica-pool payout request --asset USDT --address <addr> --amount 25

animica-pool providers
animica-pool revenue
```

Point at a different host: `animica-pool config --api https://pool.animica.org` (or `ANIMICA_POOL_API`).
