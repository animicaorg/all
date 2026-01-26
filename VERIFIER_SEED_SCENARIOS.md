# Verifier Seed Height Validation - Scenario Analysis

This document illustrates how the verifier seed height validation works in various network scenarios.

## Scenario 1: Normal Operation

**Network State:**
- Verifier Seed A (144.126.133.21): Height 100
- Verifier Seed B (3.12.224.189): Height 100
- Regular Peer 1: Height 99 (syncing)
- Regular Peer 2: Height 100 (synced)
- Miner Peer: Height 101 (just found next block)

**Result:**
- Max verifier height: 100
- Max allowed height: 101 (100 + 1)
- Network best height: **101** ✓
- **Status: VALID** - Miner's new block is accepted

**Explanation:** Both verifier seeds are at height 100, and the miner who just found block 101 is allowed to be 1 block ahead. This is the expected behavior.

---

## Scenario 2: Malicious/Misconfigured Peer

**Network State:**
- Verifier Seed A: Height 100
- Verifier Seed B: Height 100
- Regular Peer 1: Height 100
- Malicious Peer: Height 150 (claiming false height)

**Result:**
- Max verifier height: 100
- Max allowed height: 101
- Heights considered: [100, 100, 100, ~~150~~]
- Network best height: **101** (max allowed, since no valid peer at 101)
- **Status: PROTECTED** - Malicious peer ignored

**Explanation:** The malicious peer claiming height 150 is filtered out because it exceeds the verifier + 1 threshold. The network correctly ignores this invalid height.

---

## Scenario 3: Verifier Seeds Syncing

**Network State:**
- Verifier Seed A: Height 100
- Verifier Seed B: Height 105 (ahead, already synced more)
- Regular Peer 1: Height 103
- Regular Peer 2: Height 106 (1 ahead of max verifier)
- Regular Peer 3: Height 110 (too far ahead)

**Result:**
- Max verifier height: 105 (highest verifier)
- Max allowed height: 106
- Heights considered: [100, 105, 103, 106, ~~110~~]
- Network best height: **106** ✓
- **Status: VALID** - Uses highest verifier as anchor

**Explanation:** When verifiers are at different heights, the highest verifier (105) is used as the anchor. Peer at 106 is allowed (verifier + 1), but 110 is rejected.

---

## Scenario 4: Multiple Miners Finding Blocks

**Network State:**
- Verifier Seed A: Height 100
- Verifier Seed B: Height 100
- Miner A: Height 101 (just found block 101)
- Miner B: Height 101 (also found block 101 - fork scenario)

**Result:**
- Max verifier height: 100
- Max allowed height: 101
- Network best height: **101** ✓
- **Status: VALID** - Both miners at allowed height

**Explanation:** Multiple miners finding competing blocks at the same height are both allowed. Fork choice logic (not shown here) will resolve which block 101 to follow.

---

## Scenario 5: No Verifier Seeds Connected

**Network State:**
- Regular Peer 1: Height 100
- Regular Peer 2: Height 150
- Regular Peer 3: Height 200

**Result:**
- Verifier heights: [] (empty)
- No constraint applied
- Network best height: **200** (unconstrained)
- **Status: BACKWARD COMPATIBLE**

**Explanation:** When no verifier seeds are connected, the system falls back to accepting the maximum height from any peer. This ensures the system can operate in environments without verifier seeds.

---

## Scenario 6: Verifier Seeds Disabled

**Network State:**
- Environment: `ANIMICA_P2P_ENABLE_VERIFIER_SEEDS=false`
- Verifier Seed A: Height 100
- Regular Peer 1: Height 200

**Result:**
- Verifier seed feature disabled
- Network best height: **200** (unconstrained)
- **Status: DISABLED MODE**

**Explanation:** When explicitly disabled, verifier seeds are not checked even if they're connected. Useful for testing or special network configurations.

---

## Scenario 7: Network Recovering from Split

**Network State:**
- Before: Node was following a chain at height 110 (incorrect)
- Verifier Seed A connects: Height 100
- Verifier Seed B connects: Height 100

**Result:**
- Max verifier height: 100
- Max allowed height: 101
- Previous heights > 101 are now ignored
- Network best height: **101**
- **Status: RECOVERED** - Node will reorganize to correct chain

**Explanation:** When verifier seeds connect and are behind the current height, the node will recognize it's on the wrong chain and reorganize to match the verifiers.

---

## Scenario 8: Verifier Seed Behind Due to Temporary Issue

**Network State:**
- Verifier Seed A: Height 95 (temporarily behind due to restart)
- Verifier Seed B: Height 100 (normal)
- Regular Peers: Heights 99, 100, 101

**Result:**
- Max verifier height: 100 (uses highest verifier)
- Max allowed height: 101
- Network best height: **101** ✓
- **Status: RESILIENT** - Uses highest verifier

**Explanation:** The system uses the highest verifier seed as the anchor, so temporary issues with one verifier don't disrupt the network as long as another verifier is healthy.

---

## Key Takeaways

1. **Security**: Malicious peers claiming excessive heights are automatically filtered out
2. **Flexibility**: Allows legitimate miners to be 1 block ahead
3. **Resilience**: Uses highest verifier when multiple exist
4. **Backward Compatible**: Works without verifier seeds if needed
5. **Configurable**: Can be disabled or customized per environment
6. **Recovery**: Helps nodes reorganize to correct chain when verifiers reconnect

## Monitoring Recommendations

Watch for these log messages:

```
INFO: Network height constrained by verifier seeds
  max_verifier_height: 100
  unconstrained_height: 150
  constrained_height: 101
  verifier_count: 2
```

If you see frequent constraints with large differences between unconstrained and constrained heights, investigate the peers claiming excessive heights - they may be malicious or misconfigured.
