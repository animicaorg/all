/**
 * Roadmap Data
 *
 * Shipped milestones carry real dates (from docs/CHANGELOG.md) and activation
 * heights (from core/network_params.py). Future items are labelled "planned"
 * only where a repository document states the plan, and carry no dates.
 */

export type MilestoneStatus = 'done' | 'in-progress' | 'planned';

export interface RoadmapItem {
  title: string;
  description: string;
  status: MilestoneStatus;
  date?: string; // Completion date, release, or activation height (shipped items only)
  category: 'infrastructure' | 'consensus' | 'execution' | 'tooling' | 'ecosystem';
}

export interface RoadmapPhase {
  phase: string;
  description: string;
  items: RoadmapItem[];
}

export const roadmapData: RoadmapPhase[] = [
  {
    phase: "Pre-mainnet development (2025)",
    description: "The 0.x release train that built the protocol, tooling and apps before genesis. Dates are from docs/CHANGELOG.md.",
    items: [
      { title: "0.1.0 Genesis scaffolding", description: "Block and transaction formats, canonical CBOR, state DB and the first node boot path.", status: "done", date: "2025-02-28", category: "infrastructure" },
      { title: "0.2.0 RPC and SDK", description: "JSON-RPC server with the chain.*, state.* and tx.* namespaces; first Python and TypeScript client code.", status: "done", date: "2025-03-20", category: "tooling" },
      { title: "0.3.0 Python-VM contracts", description: "Deterministic Python subset, compiler to IR, gas table and interpreter.", status: "done", date: "2025-04-12", category: "execution" },
      { title: "0.4.0 Data availability", description: "Namespaced Merkle trees, erasure coding and sampling for blob data.", status: "done", date: "2025-05-05", category: "infrastructure" },
      { title: "0.5.0 Useful compute (AICF)", description: "Provider registry, job queue, SLA evaluator and the PoIES proof envelope design.", status: "done", date: "2025-05-30", category: "execution" },
      { title: "0.6.0 Peers and gossip", description: "P2P layer with post-quantum Kyber handshake, peer discovery and block/tx gossip.", status: "done", date: "2025-06-25", category: "infrastructure" },
      { title: "0.7.0 Wallets", description: "Browser extension (MV3) and Flutter wallet with ML-DSA key management.", status: "done", date: "2025-07-20", category: "ecosystem" },
      { title: "0.8.0 ZK verification", description: "Groth16, PLONK and STARK verifiers with pinned verifying keys.", status: "done", date: "2025-08-18", category: "execution" },
      { title: "0.9.0 Studio", description: "Browser contract IDE on a WASM Python VM; Studio services for deploy and verify.", status: "done", date: "2025-09-10", category: "tooling" },
      { title: "0.10.0 Install and ship", description: "Installers, signing and update flows for the desktop apps; PyPI packaging.", status: "done", date: "2025-10-01", category: "tooling" },
    ],
  },
  {
    phase: "Mainnet genesis and hardening (April to July 2026)",
    description: "Genesis, then a sequence of forward-only, height-gated forks. Heights are from core/network_params.py; release numbers from docs/ANIMICA_2026_STATE.md and docs/CHANGELOG.md.",
    items: [
      { title: "Mainnet genesis", description: "Chain id 1, genesis hash 0xa0892158…b7de, 300 ANM block subsidy, 60 s target, 81,000,000 ANM premine.", status: "done", date: "2026-04-06", category: "infrastructure" },
      { title: "Public RPC, explorer, wallets and pool", description: "rpc.animica.org, explorer.animica.org, wallet apps (non-custodial) and pool.animica.org (stratum :3333 PPS, :3334 solo).", status: "done", date: "2026", category: "ecosystem" },
      { title: "PQ hardening and root commitment", description: "FORK_PQ_HARDENING and FORK_ROOT_COMMITMENT: mandatory ML-DSA-65 verification and header root checks.", status: "done", date: "Block 40,000 (6.0.1)", category: "consensus" },
      { title: "Address freeze", description: "FORK_ADDRESS_FREEZE: validation-only rejection of blocks moving funds from the single known-compromised address.", status: "done", date: "Block 42,000 (7.0.0)", category: "consensus" },
      { title: "Foundation subsidy split", description: "FORK_FOUNDATION_SPLIT: 85% miner / 15% foundation treasury, total emission unchanged.", status: "done", date: "Block 42,001 (7.1.0)", category: "consensus" },
      { title: "Verifiable Inference Engine", description: "Proof-of-inference receipts: each AI response content-hashed and ML-DSA-65 signed, verifiable and replayable offline. Non-consensus.", status: "done", date: "2026-07-10 (7.1.1)", category: "execution" },
      { title: "State commitment (inclusion implies execution)", description: "FORK_STATE_COMMITMENT plus a value-preserving clawback migration; the rule self-gates on non-zero roots.", status: "done", date: "Block 44,444 (7.1.9)", category: "consensus" },
      { title: "Service-IOU settlement carve", description: "FORK_VPN_RELAY_REWARDS: up to 50 ANM/block carved from the miner share for service IOUs; shipped inert, emission-conserving.", status: "done", date: "Block 50,000 (8.0.1)", category: "consensus" },
      { title: "Marketplace, agent economy and dVPN", description: "8.0.x: marketplace, WireGuard exits and browser extension, ANS and easy .anm names.", status: "done", date: "2026-07-14 (8.0.0 to 8.0.3)", category: "ecosystem" },
    ],
  },
  {
    phase: "Execution and L2 (July to August 2026)",
    description: "The 9.x bundle at block 75,000 and the 10.x L2 line. The current release is 10.4.4.",
    items: [
      { title: "Bounded retarget, reorg bound, value-carrying CALL", description: "FORK_BOUNDED_RETARGET, FORK_FINALITY_DEPTH (100-block reorg bound) and FORK_VALUE_CALL.", status: "done", date: "Block 75,000 (9.5.0)", category: "consensus" },
      { title: "On-chain Python-VM execution", description: "FORK_VM_EXEC: contract CALLs execute on mainnet; below this height every CALL reverted, so history is unchanged.", status: "done", date: "Block 75,000 (9.6.0)", category: "execution" },
      { title: "Reward split 50 / 25 / 25", description: "FORK_SERVICE_CARVE and FORK_TREASURY_25: 50% miner, 25% treasury, 25% inference providers; unclaimed inference share rolls to treasury.", status: "done", date: "Block 75,000 (9.7.0)", category: "consensus" },
      { title: "Useful-work verification armed in shadow mode", description: "FORK_USEFUL_WORK_VERIFY: presence-gated; on mainnet it logs verdicts rather than enforcing. Blocks carry no proofs today.", status: "done", date: "Block 75,000 (10.2.0)", category: "consensus" },
      { title: "ANM-native L2 rollup", description: "l2/: sparse-Merkle state, parallel deterministic executor, validity by re-execution, bridge with forced exits, flat l2_* RPC. L2 chain id 1001.", status: "done", date: "10.0.0", category: "infrastructure" },
      { title: "L2 anchoring consensus-interpreted", description: "FORK_L2_ANCHOR_HEIGHT = 80,000: from this L1 height, nodes on 10.x validate L2 batch commitments in anchoring transactions.", status: "done", date: "Block 80,000 (10.x)", category: "consensus" },
      { title: "HD derivation standard", description: "docs/wallet/HD_DERIVATION.md: BIP-39 + SLIP-0010 along m/44'/4279885' to an ML-DSA-65 seed; normative for third-party wallets.", status: "done", date: "2026-08-22", category: "ecosystem" },
      { title: "x402 gateway and ANM lane", description: "x402.animica.dev: per-call HTTP 402 payments with a self-hosted facilitator and an ANM-native lane (CAIP-2 animica:1).", status: "done", date: "2026-08", category: "ecosystem" },
      { title: "MCP server", description: "pip install animica-mcp: chain reads, mining info and inference as MCP tools, no private keys.", status: "done", date: "2026-08", category: "tooling" },
    ],
  },
  {
    phase: "Documented plans (no dates)",
    description: "Items the repository documents as next steps or pending governance actions. They are plans, not commitments, and have no scheduled dates or heights.",
    items: [
      { title: "Enforcing useful-work verification", description: "A separate governance action, gated on shadow telemetry showing zero unknown-payment and incomplete-nullifier verdicts over a full window, and on committing receipts in a header root (docs/USEFUL_WORK_SHADOW_RUNBOOK.md).", status: "planned", category: "consensus" },
      { title: "Sealed header roots", description: "State-commitment enforcement stays self-gated until the miner seals real state roots network-wide (core/network_params.py).", status: "planned", category: "consensus" },
      { title: "On-chain relay contribution root", description: "The dVPN relay reward carve stays inert until an on-chain relay-registration and usage-anchoring mechanism is designed and reviewed (docs/CHANGELOG.md 8.0.1).", status: "planned", category: "consensus" },
      { title: "Node-side anchoring of ENA receipts", description: "ENA exports on-chain receipt envelopes; the node-side submission hook is documented as the next boundary (docs/ena/overview.md).", status: "planned", category: "execution" },
      { title: "SLIP-0044 and CAIP registrations", description: "Coin type 4279885 (satoshilabs/slips#2053) and CAIP-2 namespace animica (ChainAgnostic/namespaces#200) are open pull requests.", status: "in-progress", category: "ecosystem" },
      { title: "PQ algorithm rotation cadence", description: "docs/pq/POLICY.md defines semi-annual rotation with staging and grace windows; no rotation is scheduled.", status: "planned", category: "consensus" },
    ],
  },
];

export default roadmapData;
