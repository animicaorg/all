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

export const modelCatalog = [
  {
    model: 'aicf-chat-1',
    type: 'Chat Completion',
    context: '128k',
    latency: '620 ms p50',
    price: '0.72 ANM / 1K in tokens · 1.08 ANM / 1K out tokens',
  },
  {
    model: 'aicf-embed-1',
    type: 'Embeddings',
    context: '32k',
    latency: '190 ms p50',
    price: '0.22 ANM / 1K input tokens',
  },
  {
    model: 'aicf-agent-1',
    type: 'Agent Runtime',
    context: '256k',
    latency: 'job-based',
    price: 'Per step + tool execution + compute-seconds',
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

export const pricingRows = [
  {
    item: 'Chat Inference',
    unit: '1K input tokens',
    basePrice: '0.72 ANM',
    providerShare: '82%',
    treasuryShare: '18%',
  },
  {
    item: 'Chat Output',
    unit: '1K output tokens',
    basePrice: '1.08 ANM',
    providerShare: '84%',
    treasuryShare: '16%',
  },
  {
    item: 'Embeddings',
    unit: '1K input tokens',
    basePrice: '0.22 ANM',
    providerShare: '80%',
    treasuryShare: '20%',
  },
  {
    item: 'Batch Compute',
    unit: 'GPU-minute',
    basePrice: '1.95 ANM',
    providerShare: '86%',
    treasuryShare: '14%',
  },
  {
    item: 'Agent Tasks',
    unit: 'task-step',
    basePrice: '0.34 ANM',
    providerShare: '79%',
    treasuryShare: '21%',
  },
];

export const providerHardware = [
  { class: 'Entry', gpu: 'RTX 3060 / A2000', vram: '12 GB+', expected: 'Inference + embeddings' },
  { class: 'Recommended', gpu: 'RTX 4090 / L40S', vram: '24 GB+', expected: 'High-throughput inference + agents' },
  { class: 'Pro', gpu: 'A100 / H100 / MI300', vram: '80 GB+', expected: 'Enterprise latency + training jobs' },
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
