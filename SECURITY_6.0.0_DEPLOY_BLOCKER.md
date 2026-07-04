# 6.0.0 Live-Deploy Blocker — P2P network fragmentation

**Status: 6.0.0 is CODE-COMPLETE and TEST-GREEN, but MUST NOT be deployed to the
live mainnet node or published to PyPI until the P2P-fingerprint issue below is
fixed.** A trial deploy to the live `animica-mainnet-node` on 2026-07-04 was
performed and then **fully rolled back** after it fragmented the node from the
network. The node is back on the known-good pre-6.0.0 code and healthy.

## What happened (trial deploy 2026-07-04)

Overlaid the 35 changed 6.0.0 node files onto `/root/animica` and restarted. The
node came up healthy, but two independent P2P regressions isolated it (peers went
from ~8 to 0; head froze):

### Blocker 1 — L01 AEAD refuse-by-default isolates the node (FIXED in-branch)
`p2p/crypto/handshake.py` (ANM-L01) refused the pure-Python AEAD fallback unless
`cryptography` was installed. **The entire live network runs on the pure-Python
AEAD** (`cryptography` is not deployed on any peer), so the node dropped every
handshake: `HandshakeError: no production AEAD backend available … refusing the
insecure pure-Python fallback`.
- **Fix (committed):** inverted the default to **warn-and-allow**; refuse only when
  the operator opts in with `ANIMICA_STRICT_AEAD=1`. Installing `cryptography`
  (add it as a 6.0.0 runtime dependency) makes the branch unreachable and yields a
  real ChaCha20-Poly1305 AEAD that is wire-compatible with pure-Python peers.

### Blocker 2 — consensus-param hash fragments upgraded nodes (NOT yet fixed)
Even with Blocker 1 fixed, handshakes then failed with
`Peer handshake mismatch` / `consensus_mismatch` / `genesis_mismatch`. Root cause:
the P2P handshake (`p2p/node/p2p_service.py`) fingerprints the node with
`_network_params_hash()` → `core.network_params.compute_network_params_hash(chain_id)`
and `_consensus_id()`, and **these hash the fork schedule**. Adding
`FORK_ROOT_COMMITMENT` (and the new activation-height table) changed the chain-1
fingerprint, so every un-upgraded (5.3.4) peer rejects the 6.0.0 node's HELLO.

**This defeats the "forward-only, height-gated, self-gating" strategy at the P2P
layer**: a fork that is *dormant* (activation height 37000, current head ~36470)
still changes the params hash today, fragmenting the upgraded node from the network
immediately — long before activation.

## Required fix before any re-deploy (design decision needed from user)

Pick one (1 is preferred — least coordination, keeps chain working):

1. **Exclude not-yet-active / future forks from the P2P fingerprint.** Make
   `compute_network_params_hash` / `_consensus_id` hash only the genesis-anchored,
   currently-active consensus identity — NOT the forward fork schedule. Then a 6.0.0
   node and a 5.3.4 node share the same fingerprint and peer normally during the
   pre-activation rollout window. (The consensus *rules* still diverge at height
   37000 — see below — so the network must still be upgraded before then, but nodes
   can coexist and gossip until activation.)
2. **P2P compatibility shim:** accept a peer whose `network_params_hash` differs as
   long as `genesis_identity` matches and the mismatch is confined to inactive
   forks. Softer than (1); needs care not to weaken cross-network protection.
3. **Coordinated flag-day upgrade:** every node operator upgrades to 6.0.0 before
   height 37000. Simplest code, but requires network-wide coordination and a fresh
   fingerprint cutover — brittle for a live chain.

### Independent of the fingerprint: the activation itself still needs coordination
At height 37000 the new consensus rules (mandatory ml_dsa_65 sig verification,
txsRoot/proofsRoot commitment, deterministic emission) make upgraded nodes reject
blocks that un-upgraded nodes accept. That is an unavoidable property of *any*
consensus change and is why the activation is height-gated with a long runway: the
network must be substantially on 6.0.0 before 37000. The fingerprint fix (1/2 above)
is what lets upgraded and legacy nodes **coexist during that runway** instead of
partitioning the moment the first node upgrades.

## Current live state (post-rollback)
- `animica-mainnet-node`: **pre-6.0.0 code, healthy, ~8 peers, head advancing.**
- Backup of the trial deploy's pre-state: `/root/site-backups/animica-node-6.0.0-20260704-204142`.
- 6.0.0 branch (`/root/animica-sec-6.0.0`): all commits intact, tests green, NOT deployed.
- pip 6.0.0: **NOT published** (would fragment upgraders — hold until the fix lands).

## Safe path forward
1. Implement fingerprint fix (option 1) in the worktree; add a regression test that
   asserts `compute_network_params_hash(1)` is **unchanged** by adding a future,
   inactive fork.
2. Add `cryptography` as a 6.0.0 dependency (real AEAD network-wide).
3. Re-run full suite + the P2P handshake tests.
4. Re-attempt the deploy with the same backup+health+auto-rollback script, watching
   `net.peers` (must stay ~8, not drop to 0) as the go/no-go signal.
5. Only then publish pip 6.0.0 and coordinate the network upgrade ahead of H=37000.
