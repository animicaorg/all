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
    phase: "Foundation (Q4 2024 - Q1 2025)",
    description: "Core protocol implementation and local development environment",
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
    phase: "Devnet & Tooling (Q1 - Q2 2025)",
    description: "Multi-node networking, developer tools, and initial ecosystem apps",
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
        title: "Python SDK (omni_sdk)",
        description: "Full-featured SDK with wallet, transaction building, and contract deployment",
        status: "done",
        category: "tooling"
      },
      {
        title: "TypeScript SDK (@animica/sdk)",
        description: "Browser and Node.js compatible SDK for web3 integration",
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
        title: "Desktop Wallet (Flutter)",
        description: "Cross-platform wallet for key management and transaction signing",
        status: "in-progress",
        category: "ecosystem"
      },
      {
        title: "Browser Extension Wallet (MV3)",
        description: "Browser extension for dApp connectivity and transaction signing",
        status: "in-progress",
        category: "ecosystem"
      },
      {
        title: "Studio IDE (Web)",
        description: "In-browser contract editor with simulation and deployment",
        status: "done",
        category: "tooling"
      }
    ]
  },
  {
    phase: "Testnet (Q2 - Q3 2025)",
    description: "Public testnet launch with enhanced features and stability",
    items: [
      {
        title: "Multi-Node Testnet",
        description: "Public testnet with multiple validator nodes and open participation",
        status: "in-progress",
        category: "infrastructure"
      },
      {
        title: "Mining Pool Infrastructure",
        description: "Mining pool protocol and reference implementation",
        status: "in-progress",
        category: "infrastructure"
      },
      {
        title: "Testnet Faucet",
        description: "Automated faucet for test token distribution",
        status: "in-progress",
        category: "tooling"
      },
      {
        title: "Data Availability Layer",
        description: "NMT-based data availability with light client verification",
        status: "in-progress",
        category: "infrastructure"
      },
      {
        title: "AICF Integration",
        description: "AI Capability Framework for off-chain AI and quantum task coordination",
        status: "in-progress",
        category: "execution"
      },
      {
        title: "Enhanced Monitoring",
        description: "Prometheus metrics, grafana dashboards, and alerting",
        status: "planned",
        category: "tooling"
      },
      {
        title: "Rust SDK (animica-sdk)",
        description: "High-performance Rust SDK for native applications",
        status: "planned",
        category: "tooling"
      }
    ]
  },
  {
    phase: "Mainnet Preparation (Q3 - Q4 2025)",
    description: "Security audits, performance optimization, and mainnet readiness",
    items: [
      {
        title: "Security Audit",
        description: "Third-party security audit of core protocol and cryptography",
        status: "planned",
        category: "infrastructure"
      },
      {
        title: "Performance Optimization",
        description: "Transaction throughput, sync speed, and resource optimization",
        status: "planned",
        category: "infrastructure"
      },
      {
        title: "Governance Framework",
        description: "On-chain governance for parameter updates and protocol upgrades",
        status: "planned",
        category: "consensus"
      },
      {
        title: "Wallet Binary Releases",
        description: "Signed desktop and mobile wallet binaries with auto-updates",
        status: "planned",
        category: "ecosystem"
      },
      {
        title: "Documentation Completion",
        description: "Comprehensive docs for operators, developers, and users",
        status: "in-progress",
        category: "tooling"
      },
      {
        title: "Contract Standard Library",
        description: "Common contract patterns and reusable libraries",
        status: "planned",
        category: "execution"
      }
    ]
  },
  {
    phase: "Mainnet Launch (Q4 2025)",
    description: "Production mainnet with full ecosystem support",
    items: [
      {
        title: "Mainnet Genesis",
        description: "Production mainnet launch with initial validators and parameters",
        status: "planned",
        date: "Q4 2025",
        category: "infrastructure"
      },
      {
        title: "Bridge Infrastructure",
        description: "Cross-chain bridges for asset transfers",
        status: "planned",
        category: "infrastructure"
      },
      {
        title: "DEX and DeFi Primitives",
        description: "Decentralized exchange and basic DeFi contracts",
        status: "planned",
        category: "ecosystem"
      },
      {
        title: "NFT Standards",
        description: "Non-fungible token standards and marketplace support",
        status: "planned",
        category: "execution"
      },
      {
        title: "Mobile Wallet Apps",
        description: "Native iOS and Android wallet applications",
        status: "planned",
        category: "ecosystem"
      }
    ]
  },
  {
    phase: "Post-Launch (2026+)",
    description: "Ongoing development and ecosystem growth",
    items: [
      {
        title: "Zero-Knowledge Proof Support",
        description: "ZK verifier adapters for Groth16, PLONK, and STARK proofs",
        status: "planned",
        category: "execution"
      },
      {
        title: "Light Client Implementation",
        description: "Resource-efficient light clients for mobile and embedded devices",
        status: "planned",
        category: "infrastructure"
      },
      {
        title: "Advanced Contract Features",
        description: "Contract upgrades, proxies, and advanced patterns",
        status: "planned",
        category: "execution"
      },
      {
        title: "Ecosystem Grants Program",
        description: "Funding for community projects and protocol development",
        status: "planned",
        category: "ecosystem"
      },
      {
        title: "Quantum Task Marketplace",
        description: "Decentralized marketplace for quantum computing tasks",
        status: "planned",
        category: "ecosystem"
      }
    ]
  }
];

export default roadmapData;
