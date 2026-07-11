# Animica 7.1.1 upgrade announcement — fact sheet (VERIFY against deployed code)

Deployed code to check (authoritative — this is what the live node runs):
- `/root/animica/.venv/lib/python3.12/site-packages/core/network_params.py`
- `/root/animica/.venv/lib/python3.12/site-packages/consensus/rewards.py`
- `/root/animica/.venv/lib/python3.12/site-packages/execution/migrations/address_freeze_2026.py`
- `/root/animica/.venv/lib/python3.12/site-packages/core/ptl/selection.py`
Live chain: `POST http://127.0.0.1:8545/rpc` (Host: rpc.animica.org) method `chain.getHead`.
PyPI: `https://pypi.org/pypi/animica/json`.

## Claims

C1. Mainnet is chain_id **1**.
C2. **FORK_ADDRESS_FREEZE** activates at mainnet block **42_000**.
C3. **FORK_FOUNDATION_SPLIT** activates at mainnet block **42_001**.
C4. FORK_ADDRESS_FREEZE ships in pip **7.0.0**; FORK_FOUNDATION_SPLIT ships in pip **7.1.0**.
C5. **7.1.1** is the current PyPI latest and contains BOTH forks (its node consensus is
    identical to 7.1.0; the 7.1.1 delta over 7.1.0 is the non-consensus Verifiable
    Inference Engine). So `pip install -U animica` → 7.1.1 satisfies both forks.
C6. Forks are forward-only "soft tightenings": active at `height >= activation`,
    grandfathered below. Env override per fork: `ANIMICA_FORK_<NAME>_HEIGHT`.
C7. Address-freeze is a **validation-only reject rule**: at/after 42,000, block import
    rejects any block containing a non-coinbase tx whose authoritative sender OR
    recipient is a frozen account. Writes NO state. Never halts the honest chain
    (honest miners exclude frozen spends; empty/normal blocks are untouched).
C8. The frozen set is a compiled-in code constant with exactly **one** entry: the
    ANM-2026-07 attacker address (already clawed back at height 39,584, holds ~0 ANM).
    => zero economic impact on ordinary users/holders; no legitimate address is frozen.
C9. Foundation-split re-splits the SAME per-block subsidy: **85% miner / 15% foundation
    treasury**, total subsidy unchanged.
C10. Consequence for a node NOT on >=7.0.0 at block 42,000 (or not on >=7.1.0 at 42,001):
     it applies the OLD rules, so it will accept/produce blocks the upgraded majority
     rejects (or reject the new coinbase split), diverging onto a non-canonical fork —
     i.e. it "forks off" mainnet and stops tracking the real chain.
C11. Who MUST act: anyone running a full node — miners, pool operators, exchanges,
     self-hosted wallets/explorers/RPC. Users of hosted Animica services (animica.org,
     wallet.animica.org, explorer, pool front-end) need do nothing; that infra is upgraded.
C12. Upgrade path: `pip install -U animica` (delivers 7.1.1), then restart the node.
     Deadline is a BLOCK HEIGHT (42,000), not a wall-clock time.

## Anything below is a copy claim to sanity-check for overstatement / inaccuracy.
- Do NOT claim user funds are at risk (they are not — freeze = 1 attacker address).
- Do NOT promise a specific date/time (block cadence varies; height is the source of truth).
