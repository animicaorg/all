/**
 * Roadmap Data
 *
 * Development milestones and feature status.
 * Edit this file to update roadmap items and statuses.
 */

export type MilestoneStatus = 'done' | 'in-progress' | 'planned';

export interface RoadmapItem {
  title: string;
  description: string;
  status: MilestoneStatus;
  date?: string; // Optional completion/target date
  category: 'infrastructure' | 'consensus' | 'execution' | 'tooling' | 'ecosystem';
}

export interface RoadmapPhase {
  phase: string;
  description: string;
  items: RoadmapItem[];
}

export const roadmapData: RoadmapPhase[] = [
  {
    phase: "Foundation (Q4 2024 – Q1 2025)",
    description: "Core protocol implementation and local development environment — shipped",
    items: [
      {
        title: "Core Blockchain Infrastructure",
        description: "Block structure, state management, transaction handling, and CBOR serialization",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "PoIES Consensus Implementation",
        description: "Proof-of-Integrated-External-Services with hash-based mining and useful work scoring",
        status: "done",
        category: "consensus"
      },
      {
        title: "Python-VM Execution Layer",
        description: "Deterministic Python smart contract execution with gas metering and state management",
        status: "done",
        category: "execution"
      },
      {
        title: "Post-Quantum Cryptography",
        description: "Dilithium3 and SPHINCS+ signature schemes with key management",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "Local Devnet Setup",
        description: "Single-node development environment with CLI tooling",
        status: "done",
        category: "tooling"
      }
    ]
  },
  {
    phase: "Devnet & Tooling (Q1 – Q2 2025)",
    description: "Multi-node networking, developer tools, and initial ecosystem apps — shipped",
    items: [
      {
        title: "P2P Networking Layer",
        description: "Gossip protocol, peer discovery, multi-transport (TCP, QUIC, WebSocket)",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "JSON-RPC API",
        description: "Standard RPC endpoints for chain queries, transaction submission, and state access",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "Python SDK & CLI",
        description: "The animica package: wallet, transaction building, contract deployment, and mining commands",
        status: "done",
        category: "tooling"
      },
      {
        title: "TypeScript SDK",
        description: "Browser and Node.js compatible SDK for web integration",
        status: "done",
        category: "tooling"
      },
      {
        title: "Block Explorer (Web)",
        description: "Real-time explorer for blocks, transactions, addresses, and contracts",
        status: "done",
        category: "ecosystem"
      },
      {
        title: "Browser Extension Wallet (MV3)",
        description: "Browser extension for dApp connectivity and transaction signing",
        status: "done",
        category: "ecosystem"
      }
    ]
  },
  {
    phase: "Testnet (Q2 – Q3 2025)",
    description: "Public testnet with multi-node validation and pool infrastructure — shipped",
    items: [
      {
        title: "Multi-Node Testnet",
        description: "Public testnet with multiple nodes and open participation",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "Mining Pool Infrastructure",
        description: "Stratum pool protocol and reference implementation",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "Data Availability Layer",
        description: "NMT-based data availability with light client verification",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "AICF Integration",
        description: "AI compute lane for off-chain AI and quantum task coordination",
        status: "done",
        category: "execution"
      }
    ]
  },
  {
    phase: "Mainnet Launch (Q4 2025)",
    description: "Production mainnet genesis and public infrastructure — live",
    items: [
      {
        title: "Mainnet Genesis",
        description: "Production mainnet launch — the chain is live and producing blocks",
        status: "done",
        category: "infrastructure"
      },
      {
        title: "Public Explorer, Wallet & RPC",
        description: "explorer.animica.org, wallet.animica.org, and the public JSON-RPC endpoint",
        status: "done",
        category: "ecosystem"
      },
      {
        title: "Wallet Binary Releases",
        description: "Signed desktop and extension wallet builds with published checksums",
        status: "done",
        category: "ecosystem"
      }
    ]
  },
  {
    phase: "Live Operations (2026)",
    description: "Running the network in production: mining, compute, markets, and tooling",
    items: [
      {
        title: "Mining Pool with XMR Dual-Mining",
        description: "Production pool at pool.animica.org — mine ANM on CPU and dual-mine XMR on the same hardware",
        status: "done",
        date: "Q2 2026",
        category: "infrastructure"
      },
      {
        title: "Bittensor Compute Dashboard",
        description: "GPU compute integration with live dashboard at pool.animica.org/bittensor",
        status: "done",
        date: "Q2 2026",
        category: "ecosystem"
      },
      {
        title: "animica.xyz Marketplace",
        description: "Live marketplace for trading ANM and ecosystem assets",
        status: "done",
        date: "Q2 2026",
        category: "ecosystem"
      },
      {
        title: "Qt Desktop Wallet",
        description: "Native desktop wallet builds for Windows and macOS",
        status: "done",
        date: "Q2 2026",
        category: "ecosystem"
      },
      {
        title: "animica CLI 0.4.x on PyPI",
        description: `pip install animica — the complete client: mine (native CPU miner included by default), run a node, use the wallet, deploy Python contracts, run "animica up" (unified miner: PoW + useful-work + GPU train/serve + Studio), and use the Studio SDK. This is what most people want. pip install "animica[all]" adds every optional extra: Qt desktop-wallet QR codes, the full distributed Studio client (cloudpickle + omni-sdk for on-chain ANM escrow), and all server/operator deps — use it for the kitchen sink or pool/API infrastructure.`,
        status: "done",
        date: "Q2 2026",
        category: "tooling"
      },
      {
        title: "Studio Web IDE Rebuild",
        description: "The in-browser contract IDE is being rebuilt; a holding page is up meanwhile",
        status: "in-progress",
        category: "tooling"
      },
      {
        title: "Buy Desk Relaunch",
        description: "Fiat/SOL purchase flow for ANM is being reworked; card purchases are paused until it ships",
        status: "in-progress",
        category: "ecosystem"
      },
      {
        title: "wANM Solana Bridge Return",
        description: "The wrapped-ANM bridge is offline while it is hardened for relaunch",
        status: "in-progress",
        category: "infrastructure"
      }
    ]
  }
];

export default roadmapData;
