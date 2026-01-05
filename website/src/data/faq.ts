/**
 * FAQ Data
 * 
 * Frequently asked questions organized by category.
 * Edit this file to add, remove, or update FAQ entries.
 */

export interface FAQItem {
  question: string;
  answer: string;
}

export interface FAQCategory {
  category: string;
  items: FAQItem[];
}

export const faqData: FAQCategory[] = [
  {
    category: "General",
    items: [
      {
        question: "What is Animica?",
        answer: "Animica is a post-quantum layer-1 blockchain designed for security, transparency, and verifiable compute. It combines post-quantum cryptography (Dilithium3, SPHINCS+), a deterministic Python-based smart contract execution layer (Python-VM), and the PoIES (Proof-of-Integrated-External-Services) consensus mechanism that rewards both traditional mining and useful computational work."
      },
      {
        question: "What makes Animica different from other blockchains?",
        answer: "Animica stands out with its post-quantum security built-in from day one, Python-based smart contracts for easier development, and PoIES consensus that rewards meaningful computation alongside traditional mining. It also provides production-ready tooling including wallets, explorer, SDKs, and comprehensive monitoring."
      },
      {
        question: "Is Animica production-ready?",
        answer: "Animica is actively being developed with devnet and testnet environments available. The core protocol, consensus, execution layer, and supporting tools are functional. Check the roadmap for current status and upcoming mainnet milestones."
      }
    ]
  },
  {
    category: "Technical",
    items: [
      {
        question: "What is post-quantum cryptography?",
        answer: "Post-quantum cryptography refers to cryptographic algorithms that are secure against attacks from quantum computers. Animica uses Dilithium3 (ML-DSA-65) for signatures and SPHINCS+ as an alternative, both of which are quantum-resistant algorithms standardized by NIST."
      },
      {
        question: "What are Python smart contracts?",
        answer: "Animica's execution layer runs Python-based smart contracts in a deterministic virtual machine (Python-VM). This allows developers to write contracts in Python with gas metering, reproducible execution, and predictable behavior. The VM enforces determinism to ensure all nodes execute contracts identically."
      },
      {
        question: "What is PoIES consensus?",
        answer: "PoIES (Proof-of-Integrated-External-Services) is Animica's consensus mechanism that combines traditional hash-based mining with verifiable proofs of useful work (AI computation, quantum tasks, storage proofs, etc.). Miners submit both hash shares and optional proofs, with blocks accepted when the combined score exceeds a moving threshold."
      },
      {
        question: "What are block times and finality?",
        answer: "Animica targets block production based on policy parameters defined in the chain. The consensus is deterministic with probabilistic finality improving over time. Check the chain specs for the current network's target block interval."
      }
    ]
  },
  {
    category: "Development",
    items: [
      {
        question: "How do I deploy a smart contract?",
        answer: "Write your contract in Python following the Python-VM spec, compile it using the vm_py compiler, and deploy using one of our SDKs (Python, TypeScript, or Rust). See the Developers page and documentation for quickstart guides and examples."
      },
      {
        question: "Which SDKs are available?",
        answer: "Animica provides official SDKs in Python (omni_sdk), TypeScript (@animica/sdk), and Rust (animica-sdk). All SDKs support wallet management, transaction building, contract deployment, and RPC communication."
      },
      {
        question: "Is there a test network?",
        answer: "Yes, Animica provides both devnet and testnet environments for development and testing. Use the testnet faucet to request test tokens, and connect your SDK to the testnet RPC endpoint."
      },
      {
        question: "Where can I find code examples?",
        answer: "Check the SDK examples in the GitHub repository under sdk/python/examples/, sdk/typescript/examples/, and templates/ directory. The documentation also includes quickstart guides and tutorials."
      }
    ]
  },
  {
    category: "Mining & Node Operation",
    items: [
      {
        question: "How do I run a node?",
        answer: "Install dependencies using the setup script, choose your network (mainnet/testnet/devnet), and start your node using the CLI or Docker Compose. See the Node page and README for detailed instructions."
      },
      {
        question: "Can I mine with CPU or GPU?",
        answer: "Yes, Animica supports both CPU and GPU mining. The mining implementation works with standard hardware. Check the Mining page for commands and pool information."
      },
      {
        question: "What are the mining rewards?",
        answer: "Mining rewards follow the emission schedule defined in the chain parameters. Rewards are distributed to the miner's address specified in the mining configuration. Block rewards may also include transaction fees."
      },
      {
        question: "Do I need to run a node to mine?",
        answer: "You can mine solo by running your own node, or join a mining pool which runs nodes on your behalf. Solo mining gives you full control but requires running and maintaining infrastructure."
      }
    ]
  },
  {
    category: "Wallets & Security",
    items: [
      {
        question: "Which wallets support Animica?",
        answer: "Animica provides an official desktop wallet (Flutter-based) and browser extension wallet (MV3). You can also use the CLI wallet for hot and cold storage workflows. Check the Wallet page for download links."
      },
      {
        question: "How do I secure my wallet?",
        answer: "Always backup your mnemonic phrase in a secure offline location. Use hardware security modules (HSM) for production environments. Never share your private keys or mnemonic. The wallet files are encrypted, but you should still protect access to them."
      },
      {
        question: "What address format does Animica use?",
        answer: "Animica uses bech32m encoding with the 'anim1' prefix for addresses. Addresses are derived from post-quantum public keys (Dilithium3 or SPHINCS+)."
      },
      {
        question: "How do I verify downloads?",
        answer: "All official releases include checksums and signatures. Verify the SHA256 checksum of downloaded files and check the signature against the published public key. Never run unverified binaries."
      }
    ]
  }
];

export default faqData;
