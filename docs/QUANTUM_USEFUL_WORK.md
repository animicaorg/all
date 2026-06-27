# Animica Quantum Useful Work (QUW)

Quantum Useful Work is a rewarded useful-work lane: a node with a real
**Quantum RNG** contributes hardware-**attested** quantum entropy to Animica's
randomness beacon. That entropy then (1) hardens the chain's public randomness
and (2) **seeds AI work** in the ENA layer in a verifiable, anti-cheat way.
Contributors earn credit under the existing `ProofType.QUANTUM` scoring lane.

It runs with **zero hardware** via a clearly-marked, non-attested software
fallback (so the whole pipeline is testable), and auto-upgrades to the attested
hardware path when a QRNG + HSM/TPM are present.

## How it works

```
[IDQ Quantis QRNG] --bytes--> health gate (NIST SP 800-90B) --> sign (YubiHSM2/TPM2)
        |                                                              |
        +--------------- attested contribution (rand.contributeQuantumEntropy) -------+
                                                                                       v
   every node: re-run health + verify HSM/TPM signature + nonce/freshness/replay  --> ACCEPT
                                                                                       |
                 mix winning entropy into the beacon: finalize_round(qrng_bytes=...)   |
                 credit contributor via consensus.scorer.score_quantum (ProofType.QUANTUM)
                                                                                       v
                 beacon ---> AI: quantum_seed_for(job) seeds ENA training/inference
                            ---> unbiasable audit_select() of AICF receipts
```

- **Sources** (`randomness/qrng/providers.py`): `QuantisQRNG` (IDQ Quantis
  PCIe/USB, auto-discovers `/dev/qrandom0`…), `HwRngQRNG` (`/dev/hwrng`),
  `NetworkQRNG` (remote QRNG endpoint), `SoftwareFallbackQRNG` (non-attested).
  `auto_select()` picks the best, wrapped in a `HealthGatedSource`.
- **Pseudo-quantum & auto-flip (5.0.2+)** (`randomness/qrng/pseudo.py`,
  `randomness/qrng/manager.py`): with NO real provider the lane serves a
  **pseudo-quantum** source (a simulated qubit-measurement RNG, clearly
  `is_quantum=False`/non-attested) so beacons/draws/contributions keep working.
  The `EntropySourceManager` re-detects providers (`refresh()`, called each round
  by the worker; or `start_watch()`) and **flips automatically to a real attested
  QRNG the moment one connects** — a device appearing (`/dev/qrandom0`) or a
  provider registered at runtime (`connect_provider()` / `rand.connectQuantumProvider`).
  Flips are timestamped and surfaced via `rand.getQuantumMode` /
  `animica quantum quw mode`. Once flipped to real hardware + an HSM/TPM signer,
  contributions become attested with no restart.
- **Health** (`randomness/qrng/health.py`): NIST SP 800-90B Repetition Count +
  Adaptive Proportion tests and a min-entropy estimate; default gate ≥ 7.0 of 8.0
  bits/byte. Bad batches are dropped and never submitted/credited.
- **Attestation** (`randomness/qrng/hsm_tpm.py`): the trust root is a **YubiHSM 2**
  (PKCS#11) or **TPM 2.0**, not the QRNG card. It signs the domain-separated
  transcript (`animica/qrng/attest/v1`) which covers the entropy + health report +
  round nonce. Software self-signer (Ed25519) is verifiable but flagged
  non-attested → near-zero reward.
- **Contribution** (`randomness/qrng/contribution.py`): build → verify → reward
  metrics. Verification is a pure function (every node agrees): signature valid AND
  health passes AND min-entropy ≥ threshold AND nonce fresh AND not replayed.
- **Reward**: `contribution_to_quantum_metrics` →
  `{quantum_units, traps_ratio, qos}` fed unchanged into the existing
  `consensus.scorer.score_quantum` under `ProofType.QUANTUM` (no new proof type,
  no consensus renumber). `quantum_units` scales with attested entropy volume ×
  min-entropy; software fallback is bounded near zero.

## Quantum → AI (meaningful use in ENA)

`aicf/integration/quantum_seed.py` turns the (quantum) beacon into AI RNG:

- `quantum_seed_for(beacon_seed, round, job_id)` derives a **deterministic,
  verifiable** seed. ENA training (`python/animica/ena/training.py`, enable with
  `ANIMICA_ENA_QUANTUM_SEED=1`) seeds torch/numpy/python RNG from it, so weight
  init / shuffling / sampling come from attested quantum entropy. The provenance
  is recorded in the run metadata — anyone can recompute the seed from the public
  beacon, so a worker **cannot cherry-pick a favorable seed** (anti-cheat for
  AICF training/eval receipts) and runs are reproducible.
- `audit_select(receipts, beacon_seed, round, k)` picks which receipts/outputs to
  audit **unbiasably** — unpredictable before the beacon is revealed, so workers
  cannot dodge audits.

## Recommended "serious" node build

| Part | Recommendation |
|------|----------------|
| Core | Intel NUC / mini-PC / small server, 16–32 GB RAM, 1 TB NVMe, Ubuntu/Debian, dual NIC, UPS |
| QRNG | **ID Quantique Quantis QRNG PCIe 40 Mbps** or **240 Mbps** (optional USB QRNG backup) |
| Security | **YubiHSM 2**, **TPM 2.0**, full-disk encryption, locked BIOS/UEFI, read-only OS option |
| Reliability | 1U/2U rackmount, UPS, temp sensor, watchdog, remote power, log drive |

The QRNG card supplies entropy; the YubiHSM 2 / TPM 2.0 supplies the attestation
the network actually trusts. Both are needed for attested rewards.

## Install & run

```bash
pip install -U animica                      # 5.0.0+

animica quantum quw detect                  # what sources are present
animica quantum quw healthcheck --bytes 8192   # SP 800-90B health on a live batch
animica quantum quw selftest                # full local build→verify→reward roundtrip

# Production worker (systemd):
sudo cp ops/quantum/animica-quw.service /etc/systemd/system/
sudo cp ops/quantum/udev-quantis.rules /etc/udev/rules.d/99-animica-quantis.rules
sudo mkdir -p /etc/animica && sudo cp ops/quantum/quw.env.example /etc/animica/quw.env
sudo $EDITOR /etc/animica/quw.env           # set address, RPC, signer=yubihsm2
sudo udevadm control --reload && sudo udevadm trigger
sudo systemctl enable --now animica-quw
```

## Consumable verifiable randomness (5.0.1+)

The attested quantum entropy isn't just an internal reward loop — it powers a
**public, verifiable randomness service** that dApps, games, and governance can
consume. All accepted attested contributions for a round are aggregated
(`randomness/qrng/aggregate.py`, XOR-fold, contributor-bound — no single
contributor controls it) into a **quantum beacon**
(`randomness/qrng/public.py:build_quantum_beacon`). From that beacon, a family of
primitives produces outputs that are a **pure function of (beacon, request_id,
params)** — anyone recomputes and verifies them offline:

- `lottery_draw` (k distinct winners), `random_choice`, `weighted_choice`,
  `shuffle`, `random_in_range`, `coin_flip`, `dice`, `random_bytes`.
- `verify_result(result)` recomputes and confirms any draw — unbiasable
  (beacon is fixed/quantum) and unpredictable before the beacon is revealed.

Use it for fair lotteries/raffles, NFT trait/mint randomization, random
committee/auditor/validator selection, games, tie-breaks, and sampling.

```bash
animica quantum rand random --bytes 32            # attested QRNG-as-a-service
animica quantum rand lottery --entries a,b,c,d,e --k 2 --beacon <hex>
animica quantum rand draw --kind dice --sides 20 --count 3 --beacon <hex>
animica quantum rand beacon --round 42 --rpc-url http://127.0.0.1:8545/rpc
echo '<result-json>' | animica quantum rand verify -   # client-side verify
```

## RPC API

| Method | Aliases | Purpose |
|--------|---------|---------|
| `rand.getQuantumChallenge{round_id}` | `quantum.quw.getChallenge` | fresh per-round nonce |
| `rand.contributeQuantumEntropy{contribution}` | `quantum.quw.contributeEntropy` | submit attested entropy (verified + credited) |
| `rand.getQuantumCredits{address}` | `quantum.quw.getCredits` | credit units earned |
| `rand.getQuantumStatus{}` | `quantum.quw.getStatus` | lane status + detected sources |
| `rand.getQuantumBeacon{round_id}` | `quantum.quw.getBeacon` | verifiable quantum beacon (aggregated) |
| `rand.quantumDraw{round_id,request_id,kind,params}` | `quantum.quw.draw` | verifiable lottery/dice/shuffle/range/… |
| `rand.quantumRandomBytes{n,attested}` | `quantum.quw.randomBytes` | attested quantum random bytes (QRNG-as-a-service) |
| `rand.verifyQuantumResult{result}` | `quantum.quw.verify` | recompute + verify a draw |

## Security model

- QRNG entropy is **advisory/optional** to consensus (commit/reveal + VDF beacon);
  no single contributor controls the beacon. Attested contributions are preferred
  and rewarded; only one (attested-first, highest min-entropy) is mixed per round.
- Anti-replay via a domain-separated transcript nullifier; freshness via the
  server nonce + timestamp window.
- Spoofing a quantum source earns nothing: software/non-attested contributions are
  accepted (for testing) but scored near zero; only HSM/TPM-attested hardware
  earns real `quantum_units`.
```
