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
    category: "Animica & ANM",
    items: [
      {
        question: "What is Animica?",
        answer: "Animica is a live post-quantum layer-1 blockchain. It combines post-quantum signatures (ML-DSA-65, FIPS 204), a deterministic Python-based smart contract VM, and PoIES (Proof-of-Integrated-External-Services) consensus that rewards useful computational work alongside traditional mining. Mainnet is running today with a public explorer, wallets, RPC, and a mining pool."
      },
      {
        question: "What is ANM and how do I get it today?",
        answer: "ANM is Animica's native coin, used for transaction fees, contract execution, and compute settlement. Today you can earn it by mining through the pool at pool.animica.org, trade ANM/USDT on NonKYC (nonkyc.io/market/ANM_USDT), or accept ANM payments via pay.animica.dev. There is no mainnet faucet."
      },
      {
        question: "What does an anim1... address mean?",
        answer: "Animica addresses use bech32m encoding with the 'anim1' prefix — the same modern, checksummed format family used by Bitcoin taproot addresses. Addresses are derived from post-quantum public keys (ML-DSA-65), so a typo is caught by the checksum before any funds move."
      },
      {
        question: "Why post-quantum cryptography?",
        answer: "Classical signatures (ECDSA, Ed25519) can be broken by a sufficiently large quantum computer, and adversaries can record transactions now to attack them later — the 'harvest now, decrypt later' problem. Animica avoids this entirely by signing with ML-DSA-65 (FIPS 204, the successor of Dilithium3), a NIST-standardized post-quantum algorithm, from day one."
      },
      {
        question: "What are Python smart contracts?",
        answer: "Animica contracts are written in Python and run in a deterministic, gas-metered virtual machine. The VM enforces reproducible execution so every node computes the identical result. You get a mainstream language and tooling instead of a niche contract DSL."
      },
      {
        question: "Where is the code?",
        answer: "The full monorepo — node, wallets, SDKs, pool, and tooling — is public at github.com/animicaorg/all. Issues and pull requests are welcome."
      }
    ]
  },
  {
    category: "Wallets & Mining",
    items: [
      {
        question: "Which wallets exist?",
        answer: "There is a web wallet at wallet.animica.org, a desktop Qt wallet for Windows and macOS, and a browser extension wallet. The CLI (pip install animica) also includes wallet commands for hot and cold workflows. See the /wallet page for downloads and checksums."
      },
      {
        question: "What is the difference between pip install animica and animica[all]?",
        answer: "pip install animica is the complete client: everything to mine, run a node, use the wallet, deploy Python contracts, run `animica up` (the unified miner — PoW + useful-work + GPU train/serve + Studio functions), and use the Studio SDK. The native CPU miner (animica-fastpow) is included by default. This is what most people want. pip install \"animica[all]\" (keep the quotes so zsh/macOS does not glob the brackets) adds every optional extra on top: Qt desktop-wallet QR codes, the full distributed Studio client (cloudpickle for closures plus omni-sdk for on-chain ANM escrow), and all server/operator dependencies pinned. Use it if you want the kitchen sink or are running pool/API infrastructure."
      },
      {
        question: "How do I mine?",
        answer: "Point a RandomX CPU miner at the pool at pool.animica.org — and you can dual-mine XMR on the same hardware at the same time. Step-by-step setup, miner bundles, and pool stats are on the /mine page."
      },
      {
        question: "Do I need to run a node to mine?",
        answer: "No. The pool at pool.animica.org runs the chain infrastructure for you — you just connect a miner and a payout address. You can also mine solo against your own node if you prefer full control."
      },
      {
        question: "How do I secure my wallet?",
        answer: "Back up your mnemonic phrase offline and never share it. Wallet files are encrypted, but protect access to them as well. Verify the SHA256 checksum of any downloaded binary against the published checksum files before running it."
      }
    ]
  },
  {
    category: "AICF compute",
    items: [
      {
        question: "What is AICF?",
        answer: "AICF is Animica's AI-compute lane: developers buy inference and training paid in ANM, and providers earn ANM by contributing benchmarked GPU hardware. The dashboard is at aicf.animica.org and provider onboarding is at /providers."
      },
      {
        question: "Why ANM-only billing?",
        answer: "AICF settles natively on Animica. ANM billing keeps accounting, escrow, rewards, and governance on-chain and auditable."
      },
      {
        question: "How do providers earn ANM?",
        answer: "Providers run benchmarked workers, receive jobs, submit receipts, and claim rewards. Routing weight depends on uptime, quality, and stake/reputation."
      },
      {
        question: "Can smart contracts fund AI jobs?",
        answer: "Yes. Contracts can lock ANM into escrow and trigger model jobs through contract-driven workflows with dispute windows and deterministic receipts."
      },
      {
        question: "Is there a Python source option for providers?",
        answer: "Yes. AICF ships a Python bundle for providers who want source-level control, custom runtimes, and local instrumentation."
      }
    ]
  }
];

export default faqData;
