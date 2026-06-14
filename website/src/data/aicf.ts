export const howItWorks = [
  {
    title: 'Developers Fund Projects',
    description:
      'Teams top up ANM balances, mint API keys, and set model/job budgets at project level.',
  },
  {
    title: 'Scheduler Routes Work',
    description:
      'AICF routes requests to the best providers by benchmark score, latency, uptime, and reputation.',
  },
  {
    title: 'Escrow + Settlement',
    description:
      'Each job reserves ANM in escrow. Verified receipts release rewards to providers and update reputation.',
  },
  {
    title: 'Treasury Boosts Useful Work',
    description:
      'AICF treasury can subsidize critical workloads, dev grants, and bootstrap demand-side compute.',
  },
];

export const useCases = [
  {
    title: 'Inference APIs',
    description: 'Chat, classification, moderation, extraction, and agent tool-call execution.',
  },
  {
    title: 'Embeddings + Retrieval',
    description: 'Vector generation and indexing jobs for semantic search and RAG workflows.',
  },
  {
    title: 'Batch + Agent Workloads',
    description: 'Large asynchronous pipelines with per-job budget caps and callback/webhook delivery.',
  },
  {
    title: 'Training + Fine Tuning',
    description: 'Managed job orchestration for custom adaptation workloads and future model hosting.',
  },
];

// Verified against the live registry (GET https://aicf.animica.org/api/models)
// on 2026-06-10. Rates are indicative — final rates are shown at key creation.
export const modelCatalog = [
  {
    model: 'aicf-chat-1',
    type: 'Chat Completion',
    status: 'Active',
    price: '0.012 ANM / 1K input tokens · 0.018 ANM / 1K output tokens',
  },
  {
    model: 'aicf-embed-1',
    type: 'Embeddings',
    status: 'Active',
    price: '0.008 ANM / 1K input tokens · 0.00005 ANM / vector',
  },
];

export const architectureLayers = [
  {
    layer: 'Developer Interface',
    detail: 'OpenAI-compatible API, SDKs, API keys, project budgets, usage insights',
  },
  {
    layer: 'AICF Control Plane',
    detail: 'Scheduling, escrow, pricing, receipts, dispute handling, subsidy routing',
  },
  {
    layer: 'Provider Mesh',
    detail: 'GPU/CPU workers with benchmark attestations, heartbeat, and execution logs',
  },
  {
    layer: 'Animica Settlement',
    detail: 'ANM-native contracts for project balance, provider stake, rewards, and slashing',
  },
];

// Verified against the live registry (GET https://aicf.animica.org/api/models)
// on 2026-06-10: aicf-chat-1 splits 85/15, aicf-embed-1 splits 82/18.
// Rates are indicative — final rates are shown at key creation.
export const pricingRows = [
  {
    item: 'Chat Input (aicf-chat-1)',
    unit: '1K input tokens',
    basePrice: '0.012 ANM',
    providerShare: '85%',
    treasuryShare: '15%',
  },
  {
    item: 'Chat Output (aicf-chat-1)',
    unit: '1K output tokens',
    basePrice: '0.018 ANM',
    providerShare: '85%',
    treasuryShare: '15%',
  },
  {
    item: 'Chat Request Base Fee',
    unit: 'request',
    basePrice: '0.00015 ANM',
    providerShare: '85%',
    treasuryShare: '15%',
  },
  {
    item: 'Embeddings Input (aicf-embed-1)',
    unit: '1K input tokens',
    basePrice: '0.008 ANM',
    providerShare: '82%',
    treasuryShare: '18%',
  },
  {
    item: 'Embedding Vectors (aicf-embed-1)',
    unit: 'vector',
    basePrice: '0.00005 ANM',
    providerShare: '82%',
    treasuryShare: '18%',
  },
  {
    item: 'Embeddings Request Base Fee',
    unit: 'request',
    basePrice: '0.0001 ANM',
    providerShare: '82%',
    treasuryShare: '18%',
  },
];

// VRAM floors match runtimeRequirements in the live model registry:
// aicf-embed-1 needs 8 GB+ GPU memory, aicf-chat-1 needs 16 GB+.
export const providerHardware = [
  { class: 'Entry', gpu: 'RTX 3060 / A2000', vram: '8 GB+', expected: 'Embeddings (aicf-embed-1)' },
  { class: 'Recommended', gpu: 'RTX 4090 / L40S', vram: '16 GB+', expected: 'Chat inference (aicf-chat-1) + embeddings' },
  { class: 'Pro', gpu: 'A100 / H100 / MI300', vram: '24 GB+', expected: 'High-throughput inference + batch queues' },
];

export const providerSteps = [
  'Download a worker bundle for Windows, Linux, or Python source.',
  'Generate `provider.config.json` and bind your payout wallet.',
  'Run benchmark mode and verify detected GPUs + throughput score.',
  'Start worker daemon with heartbeat + logs enabled.',
  'Accept jobs, submit receipts, and track rewards in dashboard.',
];

export const faqItems = [
  {
    question: 'Why ANM-only billing?',
    answer:
      'AICF settles natively on Animica. ANM billing keeps accounting, escrow, rewards, and governance on-chain and auditable.',
  },
  {
    question: 'How do providers earn ANM?',
    answer:
      'Providers run benchmarked workers, receive jobs, submit receipts, and claim rewards. Routing weight depends on uptime, quality, and stake/reputation.',
  },
  {
    question: 'Can smart contracts fund AI jobs?',
    answer:
      'Yes. Contracts can lock ANM into escrow and trigger model jobs through contract-driven workflows with dispute windows and deterministic receipts.',
  },
  {
    question: 'Is there a Python source option for providers?',
    answer:
      'Yes. AICF ships a Python bundle for providers who want source-level control, custom runtimes, and local instrumentation.',
  },
  {
    question: 'Does AICF support training workloads?',
    answer:
      'Yes for managed job orchestration. Fine-tuning and longer training runs are supported through asynchronous job classes and budget controls.',
  },
];
