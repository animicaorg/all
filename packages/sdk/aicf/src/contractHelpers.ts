export type BuildModelCallJobInput = {
  contractAddress: string;
  requester: string;
  payer: string;
  modelId: string;
  inputRefHash: string;
  maxBudgetAnmNanos: string;
  verificationMode?: 'SINGLE_PROVIDER' | 'QUORUM_MATCH' | 'VERIFIER_REVIEW' | 'CALLBACK_ACCEPT';
  replication?: number;
  quorum?: number;
  timeoutSeconds?: number;
  challengeWindowSeconds?: number;
};

export function buildModelCallJobPayload(input: BuildModelCallJobInput): Record<string, unknown> {
  const mode = input.verificationMode ?? 'SINGLE_PROVIDER';
  const replication = input.replication ?? (mode === 'QUORUM_MATCH' ? 3 : 1);
  const quorum = input.quorum ?? (mode === 'QUORUM_MATCH' ? 2 : 1);

  return {
    contractAddress: input.contractAddress,
    requester: input.requester,
    payer: input.payer,
    modelId: input.modelId,
    jobType: 'model_call',
    inputRefHash: input.inputRefHash,
    maxBudgetAnmNanos: input.maxBudgetAnmNanos,
    timeoutSeconds: input.timeoutSeconds ?? 900,
    replication,
    quorum,
    verificationMode: mode,
    challengeWindowSeconds: input.challengeWindowSeconds ?? 300,
    providerPolicy: {
      mode: 'open',
      providerIds: []
    },
    privacy: 'private',
    callbackMode: mode === 'CALLBACK_ACCEPT' ? 'requester_accept' : 'none',
    resultType: 'json'
  };
}

export type BuildAgentTaskInput = {
  contractAddress: string;
  requester: string;
  payer: string;
  modelId: string;
  budgetAnmNanos: string;
};

export function buildAgentTaskPayload(input: BuildAgentTaskInput): Record<string, unknown> {
  return {
    contractAddress: input.contractAddress,
    requester: input.requester,
    payer: input.payer,
    modelId: input.modelId,
    budgetAnmNanos: input.budgetAnmNanos
  };
}
