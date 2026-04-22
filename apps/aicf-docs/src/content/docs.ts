export const docs = [
  {
    id: 'quickstart',
    title: 'Quickstart',
    content: [
      '1. Start AICF API, scheduler, job-worker, and provider daemon.',
      '2. Sign up on /app/onboarding and create a project.',
      '3. Fund project with ANM via wallet-linked contract call.',
      '4. Create API key and call /v1/chat/completions or /v1/embeddings.'
    ]
  },
  {
    id: 'auth',
    title: 'Auth and API Keys',
    content: [
      'Email/password and OAuth are supported for developer UX.',
      'Wallet linking is separate and required for on-chain funding/staking flows.',
      'API keys are scoped, hashed at rest, and can be revoked/rotated.'
    ]
  },
  {
    id: 'wallet',
    title: 'Wallet Connect and ANM Funding',
    content: [
      'Use Animica browser wallet provider detection (window.animica).',
      'Link wallet through signed message, then call project balance contract.',
      'Funding and staking calls are direct contract invocations from browser.'
    ]
  },
  {
    id: 'jobs',
    title: 'Jobs and Training',
    content: [
      'Job classes include inference, embeddings, training, retrieval, agent, and custom compute.',
      'Each job has ANM max budget, hardware requirements, timeout, and verification mode.',
      'Scheduler assigns jobs by capability, stake, reputation, load, and region.'
    ]
  },
  {
    id: 'contract-jobs',
    title: 'Contract Jobs (On-chain AI)',
    content: [
      'Contracts emit deterministic AI job intents and escrow ANM budgets.',
      'AICF watchers/schedulers assign providers for off-chain execution.',
      'Providers submit result commitments and references; contracts finalize payout/refund deterministically.'
    ]
  },
  {
    id: 'verification-modes',
    title: 'Verification Modes',
    content: [
      'SINGLE_PROVIDER: finalizes after challenge window if unchallenged.',
      'QUORUM_MATCH: multi-provider matching hash quorum required.',
      'VERIFIER_REVIEW: verifier acceptance required.',
      'CALLBACK_ACCEPT: requester/app explicitly accepts result before finalization.'
    ]
  },
  {
    id: 'providers',
    title: 'Provider Onboarding',
    content: [
      'Register provider profile and node metadata.',
      'Run provider daemon to heartbeat, claim jobs, and submit receipts.',
      'Rewards accrue in ANM and can be claimed via rewards contract flow.'
    ]
  },
  {
    id: 'contracts',
    title: 'VM-PY Contracts',
    content: [
      'AICFProjectBalance, AICFJobEscrow, AICFModelCall, AICFAgentTask.',
      'AICFProviderRegistry, AICFStakeManager.',
      'AICFRewards, AICFDisputeManager, AICFGovernanceConfig, optional AICFModelRegistry.',
      'Contracts are event-rich and include replay-safe settlement controls.'
    ]
  },
  {
    id: 'ops',
    title: 'Deployment and Ops',
    content: [
      'Run web, API, docs, scheduler, usage-meter, dispute-worker, treasury-worker, provider-control-plane.',
      'Run contract-job-watcher, fulfillment-scheduler, result-submitter, finalization-worker.',
      'Set internal secret for worker-to-API communication.',
      'Use admin console for grants, disputes, feature flags, and emergency pause.'
    ]
  }
];
