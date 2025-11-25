# Animica Incident Checklists
**Scope:** head stalls, forks/reorgs, mempool lockups, and P2P gossip storms.  
**Use with:** dashboards in `ops/metrics/grafana/dashboards/*.json`, alerts in `ops/metrics/rules/*`, logs via Loki/Promtail configs, and helper scripts in `ops/scripts/*`.

> General triage (always do first)
>
> 1) **Acknowledge alerts** in Prometheus/Alertmanager.  
> 2) **Snapshot state**: `kubectl get pods -n animica-devnet -o wide`, tail 200 lines of the affected components.  
> 3) **Check health** quickly with:
>    ```bash
>    bash ops/scripts/smoke_devnet.sh
>    ```
> 4) **Scope**: Single pod / all pods / region? Look at Grafana → *Node*, *PoIES*, *Mempool*, *P2P*, *DA*, *AICF*, *Randomness*.  
> 5) **Decide**: config flip vs rollout vs rollback.

---

## 1) Head stall (no new blocks)
**Symptoms**
- Head height flat; alert: *Node: head lag*. Miner hashrate drops; `WorkExpired` errors.
- PoIES acceptance near 0%; Randomness beacon may be stalled.

**Immediate checks**
```bash
# Node & miner status
kubectl logs statefulset/animica-node -n animica-devnet --tail=200
kubectl logs deploy/animica-miner -n animica-devnet --tail=200
# Quick smoke
bash ops/scripts/smoke_devnet.sh

PromQL (copy/paste)

# Head not advancing
max by (instance)(animica_head_height) - min_over_time(animica_head_height[10m]) > 0
# Acceptance rate
rate(animica_consensus_accepted_blocks_total[5m])
# Miner submissions vs rejects
sum(rate(animica_miner_shares_submitted_total[5m])) by (status)
# Beacon finalize time
histogram_quantile(0.95, sum by (le)(rate(animica_randomness_vdf_verify_seconds_bucket[10m])))

Loki queries

{app="node"} |= "ERROR" |~ "import|finalize|PoIES|Θ"
{app="randomness"} |~ "VDF|beacon finalize|commit|reveal" |= "ERROR"
{app="miner"} |= "WorkExpired" or "RPC error"

Remediation
	•	Ensure at least one healthy miner:

kubectl scale deploy/animica-miner -n animica-devnet --replicas=2


	•	Devnet only: temporarily lower difficulty Θ and/or widen acceptance window in chain params ConfigMap, then restart node:

kubectl edit configmap chain-params -n animica-devnet
kubectl rollout restart statefulset/animica-node -n animica-devnet


	•	If beacon responsible, reduce VDF target seconds/iterations in randomness/config.py-backed config map and restart randomness service (or node if co-located).
	•	If mempool empty (no tx pressure), run a synthetic load (optional):

python -m mempool.cli.replay tests/mempool/fixtures/txs_cbor --rate 5



Exit criteria
	•	Head increments ≥ 1 block interval p95 over 10m; PoIES acceptance returns to normal; beacon finalizes within round window.

Post-mortem artifacts
	•	Grafana: head, acceptance, miner submissions, beacon verify p95 (screenshots).
	•	Loki: error samples; any Θ/params diffs (commit hash).

⸻

2) Unexpected fork / deep reorg

Symptoms
	•	Diverging heads between peers; frequent reorg alerts; explorer shows branch flip-flops.

Immediate checks

# Peer & sync health
kubectl logs deploy/animica-p2p -n animica-devnet --tail=200 || true
kubectl logs statefulset/animica-node -n animica-devnet --tail=200 | grep -i "reorg\|fork\|tie-break"

PromQL

# Reorg depth counts
sum(rate(animica_consensus_reorg_events_total[10m])) by (depth)
# Peer count and RTT
avg(animica_p2p_peer_count), histogram_quantile(0.95, sum by (le)(rate(animica_p2p_rtt_seconds_bucket[5m])))

Loki

{app="p2p"} |~ "fork|inventory|headers|invalid"
{app="node"} |~ "fork choice|tie-break|reorg"

Remediation
	•	Quarantine suspicious peers (low score / invalid blocks):

python -m p2p.cli.peer ban --peer <peer_id> --minutes 60


	•	Pin to known-good peers/seeds: update ops/k8s/configmaps/seeds.yaml (or Helm values) with a reduced, trusted set; apply and restart p2p/node.
	•	Raise reorg guard (devnet): increase reorg depth alarm threshold and enable stricter tie-break logging.
	•	If a bad block propagated: clear from pending caches, roll back to common ancestor (node will do so automatically if headers validated); verify consensus validator (consensus/validator.py) logs for policy/root mismatches.

Exit criteria
	•	No reorgs > configured depth for 30m; peers stable; headers sync converges.

Post-mortem
	•	Attach locator path samples (p2p/fixtures/locator_path.json-like) and bad block hashes.

⸻

3) Mempool lockup (no ready txs / stalled sequencing)

Symptoms
	•	Many pending txs but ready=0; nonce-gaps; RBF replacements not happening; users report stuck txs despite adequate fees.

Immediate checks

python -m mempool.cli.inspect --top 20
kubectl logs deploy/animica-node -n animica-devnet --tail=200 | grep -i "mempool\|nonce\|fee"

PromQL

# Queue depth & ready count
avg(animica_mempool_size_total), avg(animica_mempool_ready_total)
# Evictions & replacements
sum(rate(animica_mempool_evictions_total[5m])) by (reason)
sum(rate(animica_mempool_replacements_total[5m]))
# Min fee watermark
avg_over_time(animica_mempool_min_fee_watermark[10m])

Loki

{app="node"} |~ "AdmissionError|FeeTooLow|NonceGap|ReplacementError"

Remediation
	•	Devnet only: lower dynamic floor or surge multiplier; ConfigMap → restart:

kubectl edit configmap node-config -n animica-devnet   # adjust mempool.* policy
kubectl rollout restart statefulset/animica-node -n animica-devnet


	•	Clear pathological gaps: ask sender to submit missing nonces; or (devnet) run:

python -m mempool.cli.flush --out /tmp/mempool_dump.cbor  # then re-inject selectively


	•	Verify RPC rate-limit not throttling wallet/relayers (rpc/middleware/rate_limit.py); temporarily relax if needed.

Exit criteria
	•	ready_total follows size_total within expected ratio; successful inclusions resume.

Post-mortem
	•	Include top senders, gap distribution, fee histogram before/after.

⸻

4) Gossip storm / P2P flood

Symptoms
	•	High CPU on p2p; dropped frames; backpressure alerts; WS disconnect rate ↑; explorer stutters.

Immediate checks

kubectl top pod -n animica-devnet
kubectl logs deploy/animica-p2p -n animica-devnet --tail=200 | grep -i "ratelimit\|drop\|backpressure"

PromQL

# Message rates & drops
sum(rate(animica_p2p_msgs_in_total[1m])) by (topic)
sum(rate(animica_p2p_msgs_dropped_total[1m])) by (reason)
# Token-bucket pressure
avg(animica_p2p_token_bucket_level)

Loki

{app="p2p"} |~ "rate limit|drop|exceeded" or "|= \"mesh\""

Remediation (progressively apply)
	1.	Tighten per-peer limits (topics/bytes/s) in p2p config (ConfigMap or Helm values) → restart p2p/node.
	2.	Reduce mesh fanout and increase backoff (GossipSub-like) in p2p/gossip/mesh.py settings.
	3.	Prioritize critical topics (headers/blocks) by crediting separate buckets; deprioritize verbose topics (shares/txs) temporarily.
	4.	Ban abusers (excessive publish rate):

python -m p2p.cli.peer ban --peer <peer_id> --minutes 120


	5.	Discovery clamp: prune noisy seeds; rotate allowlist/blocklist (ops/seeds/*), re-apply.

Exit criteria
	•	Dropped frames return to baseline; WS disconnects normalize; CPU settles; block propagation p95 < target.

Post-mortem
	•	Attach per-topic in/out charts, bucket levels, and list of banned peers.

⸻

Appendix: Rollback & smoke
	•	Helm rollback

helm history animica-devnet -n animica-devnet
helm rollback animica-devnet <REV> -n animica-devnet


	•	Compose fallback (devnet)
	•	Pin previous tags in ops/docker/docker-compose.devnet.yml, then:

docker compose -f ops/docker/docker-compose.devnet.yml --profile devnet up -d


	•	Smoke after fix

bash ops/scripts/smoke_devnet.sh



Keep this checklist nearby. If a new failure mode appears, add a Symptoms → Checks → Remediation → Exit criteria → Artifacts section and reference any new scripts/configs you introduce.
