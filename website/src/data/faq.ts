/**
 * FAQ Data
 *
 * Frequently asked questions organized by category. Answers are plain strings
 * (the page renders them as text). Facts are taken from docs/ANIMICA_2026_STATE.md,
 * docs/FAQ.md, docs/TROUBLESHOOTING.md, core/network_params.py and live reads on
 * 2026-08-23. The longer, source-cited version lives at /learn/faq.
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
    category: "Animica and ANM",
    items: [
      {
        question: "What is Animica?",
        answer:
          "Animica is an open-source proof-of-work Layer-1 blockchain (chain id 1) whose transactions are signed with the post-quantum ML-DSA-65 scheme. It runs a deterministic Python smart-contract VM, an ANM-native Layer-2 rollup, and AICF, a framework for paying AI-compute providers in ANM. Mainnet genesis was 2026-04-06, and the public explorer, RPC, pool and wallets have been live since.",
      },
      {
        question: "What is ANM?",
        answer:
          "ANM is the native coin. It pays transaction fees, contract gas, L2 fees and AI compute, and it is what miners and providers earn. It has 9 decimals: 1 ANM = 1,000,000,000 nANM, and every API and RPC amount is an integer number of nANM.",
      },
      {
        question: "How do I get ANM?",
        answer:
          "Mine it (pool.animica.org, or animica up), earn it by serving AI inference or ENA useful-work jobs, accept it as payment (pay.animica.dev), or trade for it: ANM trades on NonKYC (ANM/USDT) at https://nonkyc.io/market/ANM_USDT. There is no mainnet faucet.",
      },
      {
        question: "How much ANM will ever exist?",
        answer:
          "The code enforces a hard cap of 900,000,000 ANM. Emission started at 300 ANM per block and halves every 1,350,000 blocks (about 2.6 years at the 60-second target), down to a tail of 0.0001 ANM per block. 81,000,000 ANM was issued at genesis. Circulating supply was about 110.8 million ANM as of 2026-08-23 (explorer /api/circulating-supply).",
      },
      {
        question: "Who gets the block reward?",
        answer:
          "Since block 75,000 the subsidy is split 50% to the miner, 25% to the foundation treasury and 25% to inference providers, with any unclaimed inference share rolling to the treasury. Before that it was 85/15 miner/treasury from block 42,001 and 100% miner from genesis to 42,000. The treasury address is fixed in consensus/rewards.py and visible on the explorer.",
      },
      {
        question: "Is this investment advice or a price prediction?",
        answer:
          "No. This site states where ANM trades and what the protocol does. It never forecasts price, and nothing here is a recommendation to buy, sell or mine. ANM is volatile and could lose all value.",
      },
    ],
  },
  {
    category: "Post-quantum cryptography",
    items: [
      {
        question: "Why post-quantum signatures?",
        answer:
          "ECDSA and Ed25519, used by most chains, are expected to break against a large quantum computer, and a public ledger can be attacked retroactively. Animica uses ML-DSA-65 (FIPS 204, NIST category 3) for every account from genesis, so there is no elliptic-curve key anywhere in consensus and no migration to do later.",
      },
      {
        question: "What do 'Dilithium3' and 'ML-DSA-65' mean when docs use both?",
        answer:
          "The same scheme. Dilithium3 is the lineage name; ML-DSA-65 is its standardised form in FIPS 204. Mainnet accepts only scheme id 0x1003 (4099). Public keys are 1,952 bytes, signatures 3,309 bytes.",
      },
      {
        question: "Is SPHINCS+ supported?",
        answer:
          "Not on mainnet. SPHINCS+ (scheme id 0x1002) exists in the code and older documents as a backup, but it is consensus-stranded: a SPHINCS+ wallet cannot sign a valid mainnet transaction. Do not build on it.",
      },
      {
        question: "Why are transactions so large?",
        answer:
          "Because an ML-DSA-65 signature is 3,309 bytes and the public key travels with it. The protocol is designed around that: the transaction limit is 131,072 bytes, P2P framing is length-prefixed CBOR, and the L2 uses data-availability blobs largely to amortise signature size.",
      },
    ],
  },
  {
    category: "Wallets and addresses",
    items: [
      {
        question: "What does an anim1… address look like and why do they all start anim1zqp?",
        answer:
          "Addresses are bech32m with prefix anim. The payload is the 2-byte scheme id followed by SHA3-256 of the public key, so every ML-DSA-65 account encodes 0x1003 first, which is why mainnet account addresses are 66 characters starting anim1zqp. Contract addresses use scheme id 0x0000. Plain bech32 checksums are invalid.",
      },
      {
        question: "Which wallets exist?",
        answer:
          "A browser extension, a Qt desktop wallet for Windows and macOS, a Flutter mobile wallet, and the CLI (pip install animica, then animica wallet new). Downloads and checksums are on /downloads.",
      },
      {
        question: "Can I restore my wallet from a 12- or 24-word phrase in another wallet?",
        answer:
          "Yes, if both wallets follow docs/wallet/HD_DERIVATION.md: BIP-39 seed, SLIP-0010 hardened derivation along m/44'/4279885'/account'/0'/index' (4279885 is ASCII 'ANM'), and the 32-byte node key used directly as the ML-DSA-65 seed. The test vector for the 'abandon … about' mnemonic at index 0 is anim1zqpn54yt2fz07wg5zz33qplkh7tewv30tm5s9cdwvag6kf6myvd2d5sj9pzp7.",
      },
      {
        question: "I restored my wallet and see a different address. Are my funds lost?",
        answer:
          "Almost always no. A different address means a different derivation (another account index, a passphrase, or a wallet that predates the HD standard and stored a raw seed). Check the original wallet's export options and the derivation path; funds stay on the original address on-chain.",
      },
      {
        question: "How do I keep my wallet safe?",
        answer:
          "Write the mnemonic down offline and never type it into a website. Read every signing prompt (chain id, recipient, amount, fee); never blind-sign. Verify download checksums. Use separate keys for mining payouts and savings. Nobody from the project will ever ask for your seed phrase; we run no Discord or Telegram.",
      },
    ],
  },
  {
    category: "Transactions and fees",
    items: [
      {
        question: "What does a transfer cost?",
        answer:
          "21,000 gas at a gas price of 1 nANM, so 21,000 nANM = 0.000021 ANM on mainnet today. eth_gasPrice returns 0x1. Admission requires your balance to cover amount plus gasLimit × gasPrice.",
      },
      {
        question: "Why is there no nonce?",
        answer:
          "v2 transactions carry a validity window (validAfter and validUntil block heights) plus a random salt, and the transaction id must be unique inside that window. That gives replay protection without forcing wallets to track a counter, which matters for offline and multi-device signing. Legacy nonce-based v1 transactions are still accepted.",
      },
      {
        question: "How many confirmations should I wait for?",
        answer:
          "Animica has no finality gadget; tx.getStatus sets finalized: true after 12 confirmations as a node-side convention; read confirmations. Six confirmations (about 6 to 7 minutes) is reasonable for everyday payments. Since block 75,000 the protocol refuses reorgs deeper than 100 blocks, so more than 100 confirmations cannot be undone by consensus.",
      },
      {
        question: "My transaction was rejected. What do the error codes mean?",
        answer:
          "-32010 invalid transaction, -32011 chain-id mismatch (signed for another network), -32012 bad signature, -32013 insufficient funds for amount plus fee, -32016 gas limit too low, -32017 fee too low, -32018 transaction too large, -32020 duplicate id. Use mempool.simulateAdmission to dry-run before broadcasting.",
      },
      {
        question: "Can a transaction be reversed?",
        answer:
          "No. Once included and buried under enough confirmations it is permanent, and the project cannot reverse, block or recover transactions. Funds sent to a wrong address are gone.",
      },
    ],
  },
  {
    category: "Mining",
    items: [
      {
        question: "How do I start mining?",
        answer:
          "pip install animica, then animica up. It creates a wallet if you have none, plans what your hardware can run, and joins pool.animica.org. To use an external miner, connect to stratum+tcp://pool.animica.org:3333 (PPS) or :3334 for solo (finder keeps 95%). The full guide is at /learn/mining-guide.",
      },
      {
        question: "What algorithm is it?",
        answer:
          "SHA3-256 (NIST SHA-3, not Keccak-256) over the serialised header and nonce. A block is accepted when its PoIES score, essentially the log of its hash luck, clears the threshold Θ (thetaMicro). The CPU miner ships in the package; CUDA, OpenCL and Metal GPU backends exist in mining/. No ASICs are known.",
      },
      {
        question: "Is mining 'useful work'?",
        answer:
          "Not on mainnet today. PoIES defines how verified AI, quantum, storage and VDF proofs would add to a block's score, but the reference miner attaches no proofs and the verification rule runs in shadow mode, so blocks are accepted on hash work alone. Useful work that pays today is the ENA job layer and AICF inference serving, which are paid through the pool and AICF, not enforced by consensus.",
      },
      {
        question: "Do I need to run a node to mine?",
        answer:
          "No. The pool runs the chain infrastructure; you connect a miner and a payout address. Mining directly against your own templates does require a node.",
      },
      {
        question: "Why does my pool hashrate show 0 while the network hashrate is high?",
        answer:
          "pool_hashrate counts miners connected to the pool; network_hashrate_hps is derived from Θ and block times for the whole network, including solo and direct miners. The network was about 52 MH/s on 2026-08-23.",
      },
      {
        question: "Why are blocks slower than 60 seconds?",
        answer:
          "Θ retargets with an exponential moving average, bounded per step since block 75,000, so after a hashrate drop blocks run slow until Θ catches up. The explorer measured an average of about 67 to 70 seconds on 2026-08-23.",
      },
    ],
  },
  {
    category: "Nodes and the network",
    items: [
      {
        question: "How do I run a node?",
        answer:
          "pip install animica, animica network set mainnet, animica node up. RPC listens on 127.0.0.1:8545 at the /rpc path, P2P on 30333, metrics on 9000. Data lives in ~/.animica/chain-1. See /node and /learn/run-a-node.",
      },
      {
        question: "What hardware does a node need?",
        answer:
          "The node is pure Python and CPU-bound on signature verification, so a fast core and an SSD matter most. The repository's multi-node Docker guide assumes 8 GB RAM and 20 GB disk for a three-node setup; a single mainnet node needs less.",
      },
      {
        question: "What is the public RPC and what are its limits?",
        answer:
          "POST https://rpc.animica.org/rpc, JSON-RPC 2.0, CORS *. The /rpc path is required; GET is not served; there is no WebSocket. It is rate-limited and operated by the project. For sustained or trust-critical use, run your own node.",
      },
      {
        question: "What happens at a fork height if I have not upgraded?",
        answer:
          "For a reject rule your node stops at the first violating block and must be upgraded and restarted. For an emission change your node keeps following the chain but records wrong balances, silently. Upgrade before every announced height: pip install --upgrade animica.",
      },
      {
        question: "Why does the head show all-zero state and transaction roots?",
        answer:
          "Sealed header roots are armed but not yet populated by the reference miner, and the state-commitment rule self-gates on non-zero roots. It is expected, not a fault. Validity comes from every node executing every block.",
      },
    ],
  },
  {
    category: "Building on Animica",
    items: [
      {
        question: "What language are smart contracts written in?",
        answer:
          "A strict subset of Python: ints, bytes, bools and addresses; if/while/for-range; integer and bit arithmetic; imports only from stdlib (storage, events, hash, abi, treasury, syscalls). No floats, I/O, time, random or eval. Storage values are bytes. On-chain execution has been live since block 75,000.",
      },
      {
        question: "Which SDKs exist?",
        answer:
          "The animica Python package (CLI plus client), the in-repo Python SDK (sdk/python, omni_sdk) with transaction builders and contract clients, a TypeScript SDK (sdk/typescript, @animica/sdk) for Node 18+ and browsers, and a Rust client. The JSON-RPC surface is small enough to call with plain HTTP too.",
      },
      {
        question: "What is the L2?",
        answer:
          "An ANM-only payment rollup (10.x, l2/) with ML-DSA-65 signatures end to end, a designated sequencer, validity by re-execution from published DA blobs, a bridge that never lets L2 ANM exceed L1 ANM locked, and forced exits via L1. L2 chain id is 1001. Methods are flat l2_* on the same RPC server.",
      },
      {
        question: "How do I give an AI agent access to Animica?",
        answer:
          "pip install animica-mcp and add the animica-mcp command to your MCP client (Claude Desktop, Claude Code, Cursor). It exposes chain reads, mining information and inference as tools and holds no private keys. For per-call paid access without accounts, use the x402 gateway at x402.animica.dev.",
      },
    ],
  },
  {
    category: "AI compute: AICF and ENA",
    items: [
      {
        question: "What is AICF?",
        answer:
          "The AI Compute Framework: a provider registry with staking, heartbeats and SLA scoring, a job queue, and settlement in ANM. Inference requests to animica.dev/v1 become AICF jobs served by registered workers, which return ML-DSA-65-signed proof-of-inference receipts. The registry is small today (one provider on 2026-08-23); the live count is on /status.",
      },
      {
        question: "Is there really a free AI API?",
        answer:
          "Yes. https://animica.dev/v1 is OpenAI-compatible, needs no key or signup, and is limited to 30 requests per minute per IP. It is funded by the foundation treasury and served by the miner network. Check each model's serving flag in /v1/models; a model with no worker online can queue or return 503.",
      },
      {
        question: "How do I earn by serving inference?",
        answer:
          "animica miner aicf-worker start --address anim1zqp… --tiers standard,small,flagship registers a worker and claims jobs. Per-job revenue follows the registry rates (85% provider / 15% treasury on aicf-chat-1), and since block 75,000 a 25% share of each block subsidy is reserved for inference providers. See /providers and /compute-pricing.",
      },
      {
        question: "What is ENA?",
        answer:
          "The CLI-first layer for agent runtime, retrieval, useful-work jobs and training orchestration inside the animica package. Contributors run animica ena worker start on ordinary machines, complete CPU-friendly jobs (scrape, extract, chunk, embed, index, summarise and so on), and are paid in ANM through the pool on verified receipts. On 2026-08-23 it reported about 146,000 verified jobs from 71 contributors.",
      },
      {
        question: "Can a contract call an AI model?",
        answer:
          "The contract syscalls (ai_enqueue, read_result) and the next-block result pattern are specified in docs/aicf/CLIENT_GUIDE.md. For production work today, use the HTTP API or a paid key from console.animica.org; treat the contract path as specified rather than battle-tested.",
      },
    ],
  },
  {
    category: "Project and safety",
    items: [
      {
        question: "Where is the code and under what licence?",
        answer:
          "github.com/animicaorg/all (monorepo) and github.com/animicaorg/animica-core. The repository LICENSE is Apache-2.0; docs/legal/LICENSING.md also offers MIT for most packages, the website is MIT and docs/ is CC BY-SA 4.0. The name and logo are trademarks and are not open-licensed.",
      },
      {
        question: "Who runs Animica and how are decisions made?",
        answer:
          "A small group of maintainers develops it in the open. Protocol changes follow the AIP process in docs/governance (proposal issue, RFC, 7- or 14-day review, last call, signed bundle with a timelock) and ship as height-gated releases. There is no token-vote DAO.",
      },
      {
        question: "How do I report a security issue?",
        answer:
          "Email security@animica.org; do not open a public issue. The policy commits to acknowledging within 3 business days and assessing severity within 7 days, with a typical fix window of 30 to 90 days and a safe harbour for good-faith research. Details on /security and in /.well-known/security.txt.",
      },
      {
        question: "Has the code been audited?",
        answer:
          "No third-party audit report is published in the repository, so this site does not claim one. The governance documents require security and economics review for consensus and VM changes, and several forks were reviewed adversarially in-house before activation; that is not the same as an independent audit.",
      },
      {
        question: "Where do I get help?",
        answer:
          "Start with /learn/faq, /learn/mining-guide and /learn/run-a-node, then GitHub issues for bugs and contact@animica.org for anything private. There is no paid support tier or SLA. See /support for what to include in a report.",
      },
    ],
  },
];

export default faqData;
