# Alerts Reference — What It Means & First Steps

This guide maps each Prometheus alert in **Animica** to: meaning, likely causes, fast triage, dashboards/logs to check, and exit criteria. Use alongside:
- **Dashboards**: `ops/metrics/grafana/dashboards/*.json`
- **Alert rules**: `ops/metrics/rules/*.yaml` (and `ops/docker/config/rules/*.yaml`)
- **Runbooks**: `ops/runbooks/incident_checklist.md`
- **Helper scripts**: `ops/scripts/*.sh`

---

## Legend

- **Grafana panels:** *Node*, *PoIES*, *Mempool*, *P2P*, *DA*, *AICF*, *Randomness*, *Explorer*
- **Loki labels:** `{app="node"}`, `{app="p2p"}`, `{app="da"}`, `{app="aicf"}`, `{app="randomness"}`, `{app="explorer"}`
- **Quick smoke:** `bash ops/scripts/smoke_devnet.sh`

---

## Node / Core

### Alert: **NodeHeadLagHigh**
**Meaning:** Local head height is behind network/expected by threshold.  
**Likely causes:** stalled miner input, slow consensus acceptance, P2P isolation, DB stalls.  
**First steps:**
1. Check head & miner logs:
   ```bash
   kubectl logs statefulset/animica-node -n animica-devnet --tail=200 | grep -i "head\|import\|finalize"
   kubectl logs deploy/animica-miner -n animica-devnet --tail=200

	2.	Grafana → Node (head), PoIES (acceptance), P2P (peers/RTT).
	3.	Smoke test: bash ops/scripts/smoke_devnet.sh.
Exit when: head catches up and advances at p95 interval for 10m.

⸻

Alert: NodeRPCErrorRateHigh

Meaning: Elevated 5xx/timeout rate on JSON-RPC.
Likely causes: hot paths (tx send / block lookups), rate-limit caps, DB I/O pressure.
First steps: Grafana → Explorer/Node latency/error panels, Loki {app="node"} |= "ERROR" |= "rpc". Consider temporarily relaxing RPC rate limits (devnet).
Exit when: error rate < SLO for 15m.

⸻

Alert: NodeMemoryHigh / NodeCPUHigh

Meaning: Resource saturation on node container.
Likely causes: heavy P2P, DA verification bursts, explorer scraping.
First steps: kubectl top pod -n animica-devnet, check Node dashboard; bump resources or scale read replicas.
Exit when: utilization < target for 15m.

⸻

Alert: PeersLow

Meaning: Peer count below minimum for healthy sync/gossip.
Likely causes: broken seeds/bootstrap, firewall, banned by many peers.
First steps: Verify seeds config, rotate seeds (ops/scripts/gen_bootstrap_list.py), Loki {app="p2p"} |= "dial" or "ban".
Exit when: peers >= minimum threshold for 10m.

⸻

PoIES / Consensus

Alert: PoIESGammaStuck

Meaning: Γ (useful-work budget) reads as flat/zero; no mix contribution.
Likely causes: policy misconfig, proof selection starvation, miner not attaching useful proofs.
First steps: PoIES dashboard (Γ/mix), Mining metrics, Loki {app="node"} |~ "PoIES|caps|Γ". On devnet, relax caps in consensus/policy ConfigMap and restart.
Exit when: Γ resumes non-zero trajectory.

⸻

Alert: PoIESFairnessCollapse

Meaning: One proof type dominating beyond fairness bands.
Likely causes: α-tuner disabled/misconfigured, escort/diversity caps not applied.
First steps: Check PoIES fairness panel; ensure consensus/alpha_tuner logs; validate policy hash matches chain.
Exit when: shares per type return within fairness envelope for a full window.

⸻

Alert: PoIESZeroMix

Meaning: Accepted blocks lack useful proof mix.
Likely causes: miners not producing AI/Quantum/Storage/VDF proofs, or rejection upstream.
First steps: Inspect miner logs for proof_selector decisions; verify proofs/ validators pass in node logs.
Exit when: mix > 0 for consecutive N blocks.

⸻

Alert: ConsensusAcceptanceLow

Meaning: Acceptance rate vs Θ (difficulty) is unusually low.
Likely causes: Θ too high, mempool empty, bad header templates.
First steps: PoIES acceptance, Mempool ready size; devnet: reduce Θ in params; confirm miner templates rotate.
Exit when: acceptance returns to baseline.

⸻

Mempool

Alert: MempoolQueueDepthHigh

Meaning: Queue length exceeds threshold.
Likely causes: low inclusion rate, fee floor too high, nonce gaps, DoS.
First steps: python -m mempool.cli.inspect --top 20; Mempool dashboard; Loki {app="node"} |~ "AdmissionError|NonceGap|FeeTooLow". Consider lowering floor/surge (devnet).
Exit when: depth drops below watermark.

⸻

Alert: MempoolRejectionsSpike

Meaning: Rejected tx rate spiking.
Likely causes: chainId mismatch, low fee submitter, malformed CBOR.
First steps: sample rejects from logs; check RPC clients versions; ensure wallet/SDK chainId matches.
Exit when: rejection rate returns to baseline.

⸻

Alert: MempoolReplacementStall

Meaning: RBF not progressing; many stuck nonces.
Likely causes: replacement threshold too high.
First steps: verify mempool/policy thresholds; advise users to bump effective fee >= policy; optionally relax threshold (devnet).
Exit when: ready queue forms and inclusions resume.

⸻

P2P

Alert: P2PGossipBackpressureHigh

Meaning: Outbound queue pressure indicates flood or slow consumers.
Likely causes: mesh fanout too large, abusive peer(s), slow disk.
First steps: P2P dashboard (drops/backpressure), Loki {app="p2p"} |= "drop|rate limit|backpressure"; temporarily reduce fanout & raise backoff, ban abusers.
Exit when: pressure/drops normalize.

⸻

Alert: P2PRTT95thHigh

Meaning: Network latency degrading.
Likely causes: regional issues, peer set shift.
First steps: check RTT distribution per region ASN (if labeled), prefer closer seeds; temporarily pin to nearby peers.
Exit when: p95 RTT within SLO.

⸻

Alert: P2PDroppedFramesHigh

Meaning: Many frames dropped (validation/rate limits).
Likely causes: misbehaving peers or oversized topics (shares, blobs).
First steps: lower per-topic limits; isolate high-rate peers; verify validators.
Exit when: drops near baseline.

⸻

Data Availability (DA)

Alert: DAPFailRateHigh

Meaning: Light-client DAS failure probability (p_fail) above target.
Likely causes: insufficient sampling, erasure coding errors, NMT proof issues.
First steps: DA dashboard (sampling), Loki {app="da"} |~ "proof|nmt|erasure"; validate da/erasure/params.
Exit when: p_fail below threshold.

⸻

Alert: DANMTProofErrorSpike

Meaning: NMT proof verification errors increased.
Likely causes: index calc bugs, namespace violations, bad leaves.
First steps: run da/tests/test_nmt_proofs.py locally against suspect data; inspect tree-building logs.
Exit when: error rate drops to baseline.

⸻

Alert: DARetrievalErrorsHigh

Meaning: REST retrieval 4xx/5xx spikes.
Likely causes: rate limiting, cache cold, store pressure.
First steps: DA API latency/errors; expand cache or store IOPS; adjust buckets.
Exit when: error rate normalizes.

⸻

AICF (AI Compute Fund)

Alert: AICFJobTimeoutRateHigh

Meaning: Jobs expiring or providers not returning proofs.
Likely causes: provider outages, SLA breach, queue congestion.
First steps: AICF dashboard (queue/leases), Loki {app="aicf"} |~ "timeout|lease|retry"; reassign or reduce quotas; notify providers.
Exit when: timeout rate below SLO.

⸻

Alert: AICFSLABreachRateHigh

Meaning: Providers failing traps/QoS thresholds.
Likely causes: degraded hardware, misconfig.
First steps: check SLA evaluator metrics; consider jailing/slashing per policy; route jobs away.
Exit when: SLA breaches < threshold.

⸻

Alert: AICFSlashingBurst

Meaning: Sudden many slashes.
Likely causes: systemic config or attack.
First steps: pause new assignments (reduce quotas), investigate provider common factor; verify traps pipeline.
Exit when: slash rate stabilizes; root cause fixed.

⸻

Randomness / Beacon

Alert: BeaconStalled

Meaning: Round did not finalize within window.
Likely causes: no reveals, VDF not submitted/verified, schedule drift.
First steps: Randomness dashboard; Loki {app="randomness"} |~ "commit|reveal|VDF|finalize"; widen grace (devnet) or lower VDF iterations; ensure miners run VDF worker.
Exit when: next round finalizes on time.

⸻

Alert: VDFVerifyFailuresHigh

Meaning: VDF proofs failing verification.
Likely causes: bad parameters/iterations, wrong discriminant, corrupted proofs.
First steps: run randomness/cli/verify_vdf.py on latest; compare params vs spec/params.yaml.
Exit when: verify success rate returns to normal.

⸻

Alert: CommitRevealImbalance

Meaning: Many commits but few reveals (or vice versa).
Likely causes: mis-timed clients, window confusion.
First steps: verify schedule exports, announce current round/ETA via WS; consider widening grace (devnet).
Exit when: commit→reveal ratios normalize.

⸻

Explorer / API Surfacing

Alert: ExplorerAPI5xxRateHigh

Meaning: Explorer API serving elevated 5xx.
Likely causes: backend RPC failures, high fanout.
First steps: Explorer & Node dashboards; Loki {app="explorer"} |= "ERROR".
Exit when: 5xx < SLO.

⸻

Alert: ExplorerWSDisconnectRateHigh

Meaning: Websocket disconnects above threshold.
Likely causes: network instability, backpressure, CORS/rate limits too strict.
First steps: review WS hub logs; relax limits (devnet) and observe.
Exit when: disconnect rate normalizes.

⸻

Quick Triage Snippets

PromQL scratchpad

# Head movement last 10m
max(animica_head_height) - min_over_time(animica_head_height[10m])

# P2P drops by reason
sum(rate(animica_p2p_msgs_dropped_total[5m])) by (reason)

# Mempool size & ready
avg(animica_mempool_size_total), avg(animica_mempool_ready_total)

# AICF timeouts
sum(rate(animica_aicf_job_timeouts_total[10m]))

Loki scratchpad

{app="node"} |= "ERROR"
{app="p2p"} |~ "rate limit|drop|backpressure"
{app="da"} |~ "proof|nmt|erasure"
{app="aicf"} |~ "timeout|SLA|slash"
{app="randomness"} |~ "VDF|beacon|reveal"


⸻

When to Escalate
	•	Repeated BeaconStalled or NodeHeadLagHigh for > 30m despite remediation.
	•	AICFSlashingBurst with shared provider root cause unidentified.
	•	PoIESFairnessCollapse persisting across multiple retarget windows.
	•	DAPFailRateHigh with confirmed correct erasure/NMT params.

Document findings in the incident ticket with:
	•	Timestamps, dashboards screenshots, alert list, remediation diffs (config/rollout IDs), and follow-up actions.

⸻

