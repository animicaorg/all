import {
  ANM_NANOS,
  DEFAULT_MODELS,
  calculateCharge,
  chooseProvider,
  createId,
  ensureBudget,
  estimateTokenCount,
  formatAnmNanos,
  hashPassword,
  hashSecret,
  nowIso,
  parseAnmNanos,
  randomSecret,
  type AccountUser,
  type AgentTaskRecord,
  type ApiKeyRecord,
  type ApiKeyScope,
  type AuditLogEntry,
  type ContractDisputeRecord,
  type ContractArtifactRecord,
  type ContractJobChainEvent,
  type ContractJobRecord,
  type ContractJobRequest,
  type ContractJobState,
  type ContractRecord,
  type ContractVerificationMode,
  type EscrowEventRecord,
  type FeatureFlag,
  type JobRecord,
  type JobAssignmentRecord,
  type JobRequestPayload,
  type ModelDefinition,
  type Project,
  type ProviderNode,
  type ProviderNodeCapability,
  type ProviderProfile,
  type ResultCommitmentRecord,
  type SettlementRecord,
  type TreasurySnapshot,
  type UsageRecord,
  deterministicPseudoEmbedding
} from '@animica/aicf-shared';
import jwt from 'jsonwebtoken';
import type { AppConfig } from '../config.js';
import type { AppLogger } from '../logger.js';
import type { InMemoryStore } from '../store/inMemoryStore.js';

type SessionClaims = {
  uid: string;
  role: string;
};

export type AuthUser = {
  id: string;
  email: string;
  role: AccountUser['role'];
  wallet?: AccountUser['wallet'];
};

export type ChatCompletionInput = {
  model: string;
  messages: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>;
  max_tokens?: number;
  stream?: boolean;
  metadata?: Record<string, unknown>;
};

export type EmbeddingInput = {
  model: string;
  input: string | string[];
  dimensions?: number;
};

export type ProviderRuntimeSubmission = {
  output: Record<string, unknown>;
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
    embeddingVectors?: number;
    latencyMs?: number;
    bytesIn?: number;
    bytesOut?: number;
  };
};

function cloneProject(project: Project): Project {
  return JSON.parse(JSON.stringify(project)) as Project;
}

function cloneDeep<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function sanitizeUser(user: AccountUser): AuthUser {
  return {
    id: user.id,
    email: user.email,
    role: user.role,
    wallet: user.wallet
  };
}

function splitPasswordHash(stored: string): { salt: string; hash: string } {
  const [salt, hash] = stored.split(':');
  if (!salt || !hash) {
    throw new Error('Corrupted password hash format');
  }
  return { salt, hash };
}

function makePasswordHash(password: string): string {
  const salt = randomSecret('salt').slice(0, 24);
  const hash = hashPassword(password, salt);
  return `${salt}:${hash}`;
}

function checkPassword(password: string, stored: string): boolean {
  const { salt, hash } = splitPasswordHash(stored);
  return hashPassword(password, salt) === hash;
}

function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function defaultFlags(): FeatureFlag[] {
  return [
    { key: 'aicf.scheduler.enabled', enabled: true },
    { key: 'aicf.training.enabled', enabled: true },
    { key: 'aicf.admin.pause_enabled', enabled: true },
    { key: 'aicf.disputes.enabled', enabled: true },
    { key: 'aicf.treasury.subsidy_enabled', enabled: true },
    { key: 'aicf.contract_jobs.enabled', enabled: true },
    { key: 'aicf.contract_jobs.finalization.enabled', enabled: true },
    { key: 'aicf.contract_jobs.watcher.enabled', enabled: true }
  ];
}

export class PlatformService {
  constructor(
    private readonly store: InMemoryStore,
    private readonly config: AppConfig,
    private readonly logger: AppLogger
  ) {
    this.bootstrap();
  }

  private bootstrap(): void {
    if (this.store.models.size === 0) {
      for (const model of DEFAULT_MODELS) {
        this.store.models.set(model.name, model);
      }
    }

    if (this.store.featureFlags.size === 0) {
      for (const flag of defaultFlags()) {
        this.store.featureFlags.set(flag.key, flag);
      }
    }

    if (this.store.treasurySnapshots.size === 0) {
      const snapshot: TreasurySnapshot = {
        id: createId('treasury'),
        availableAnmNanos: formatAnmNanos(BigInt(this.config.AICF_TREASURY_BOOTSTRAP_ANM) * ANM_NANOS),
        allocatedSubsidyAnmNanos: '0',
        paidProviderAnmNanos: '0',
        protocolFeesAnmNanos: '0',
        grantsOutstandingAnmNanos: '0',
        createdAt: nowIso()
      };
      this.store.treasurySnapshots.set(snapshot.id, snapshot);
    }

    if (!this.store.usersByEmail.has(normalizeEmail(this.config.AICF_ADMIN_BOOTSTRAP_EMAIL))) {
      const admin = this.createUser({
        email: this.config.AICF_ADMIN_BOOTSTRAP_EMAIL,
        password: this.config.AICF_ADMIN_BOOTSTRAP_PASSWORD,
        role: 'admin',
        oauthProvider: undefined
      });
      this.audit('system', 'system', 'bootstrap_admin', 'user', admin.id, {
        email: admin.email
      });
    }

    if (![...this.store.providers.values()].some((provider) => provider.id === 'provider_first_party')) {
      const firstParty: ProviderProfile = {
        id: 'provider_first_party',
        userId: 'system',
        walletAddress: this.config.AICF_TREASURY_ADDRESS,
        daemonTokenHash: 'system',
        state: 'active',
        reputation: 99,
        totalJobsCompleted: 0,
        totalJobsFailed: 0,
        stakeAnm: formatAnmNanos(BigInt(this.config.AICF_MIN_PROVIDER_STAKE_ANM) * ANM_NANOS),
        slashHistory: [],
        createdAt: nowIso()
      };
      this.store.providers.set(firstParty.id, firstParty);
    }
  }

  private latestTreasurySnapshot(): TreasurySnapshot {
    const snapshots = [...this.store.treasurySnapshots.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const last = snapshots[snapshots.length - 1];
    if (!last) {
      throw new Error('Treasury not initialized');
    }
    return last;
  }

  private writeTreasurySnapshot(next: Partial<TreasurySnapshot>): TreasurySnapshot {
    const current = this.latestTreasurySnapshot();
    const merged: TreasurySnapshot = {
      ...current,
      ...next,
      id: createId('treasury'),
      createdAt: nowIso()
    };
    this.store.treasurySnapshots.set(merged.id, merged);
    return merged;
  }

  private getModel(name: string): ModelDefinition {
    const model = this.store.models.get(name);
    if (!model) {
      throw new Error(`Unknown model '${name}'`);
    }
    if (model.status !== 'active') {
      throw new Error(`Model '${name}' is not active`);
    }
    return model;
  }

  private getProject(projectId: string): Project {
    const project = this.store.projects.get(projectId);
    if (!project) {
      throw new Error('Project not found');
    }
    return project;
  }

  private adjustProjectBalance(
    project: Project,
    delta: {
      availableAnmNanos?: bigint;
      reservedAnmNanos?: bigint;
      spentAnmNanos?: bigint;
      refundedAnmNanos?: bigint;
      depositedAnmNanos?: bigint;
    }
  ): void {
    const available = parseAnmNanos(project.balance.availableAnm) + (delta.availableAnmNanos ?? 0n);
    const reserved = parseAnmNanos(project.balance.reservedAnm) + (delta.reservedAnmNanos ?? 0n);
    const totalSpent = parseAnmNanos(project.balance.totalSpentAnm) + (delta.spentAnmNanos ?? 0n);
    const totalRefunded = parseAnmNanos(project.balance.totalRefundedAnm) + (delta.refundedAnmNanos ?? 0n);
    const totalDeposited = parseAnmNanos(project.balance.totalDepositedAnm) + (delta.depositedAnmNanos ?? 0n);

    if (available < 0n || reserved < 0n || totalSpent < 0n || totalRefunded < 0n || totalDeposited < 0n) {
      throw new Error('Invalid project balance mutation');
    }

    project.balance.availableAnm = formatAnmNanos(available);
    project.balance.reservedAnm = formatAnmNanos(reserved);
    project.balance.totalSpentAnm = formatAnmNanos(totalSpent);
    project.balance.totalRefundedAnm = formatAnmNanos(totalRefunded);
    project.balance.totalDepositedAnm = formatAnmNanos(totalDeposited);
    project.updatedAt = nowIso();
  }

  private requireProjectFunds(project: Project, amount: bigint): void {
    const available = parseAnmNanos(project.balance.availableAnm);
    if (available < amount) {
      throw new Error('Insufficient ANM project balance');
    }
  }

  private createUsageAndSettlement(input: {
    jobId?: string;
    projectId: string;
    apiKeyId?: string;
    modelName: string;
    className: JobRequestPayload['class'];
    providerId: string;
    status: 'success' | 'error';
    inputTokens: number;
    outputTokens: number;
    embeddingVectors: number;
    latencyMs: number;
    bytesIn: number;
    bytesOut: number;
    subsidyBps: number;
  }): { usage: UsageRecord; settlement: SettlementRecord } {
    const model = this.getModel(input.modelName);
    const charge = calculateCharge({
      pricing: model.pricing,
      inputTokens: input.inputTokens,
      outputTokens: input.outputTokens,
      embeddingVectors: input.embeddingVectors,
      subsidyBps: input.subsidyBps
    });

    const usage: UsageRecord = {
      id: createId('usage'),
      projectId: input.projectId,
      apiKeyId: input.apiKeyId,
      jobId: input.jobId,
      model: input.modelName,
      class: input.className,
      providerId: input.providerId,
      status: input.status,
      inputTokens: input.inputTokens,
      outputTokens: input.outputTokens,
      embeddingVectors: input.embeddingVectors,
      latencyMs: input.latencyMs,
      bytesIn: input.bytesIn,
      bytesOut: input.bytesOut,
      chargedAnmNanos: formatAnmNanos(charge.netChargeAnmNanos),
      providerRewardAnmNanos: formatAnmNanos(charge.providerRewardAnmNanos),
      treasuryCutAnmNanos: formatAnmNanos(charge.treasuryCutAnmNanos),
      subsidyAnmNanos: formatAnmNanos(charge.subsidyAnmNanos),
      createdAt: nowIso()
    };

    const settlement: SettlementRecord = {
      id: createId('settlement'),
      jobId: input.jobId ?? createId('instant_job'),
      providerId: input.providerId,
      projectId: input.projectId,
      chargeAnmNanos: usage.chargedAnmNanos,
      providerRewardAnmNanos: usage.providerRewardAnmNanos,
      treasuryCutAnmNanos: usage.treasuryCutAnmNanos,
      subsidyAnmNanos: usage.subsidyAnmNanos,
      status: 'queued_onchain',
      createdAt: nowIso()
    };

    this.store.usage.set(usage.id, usage);
    this.store.settlements.set(settlement.id, settlement);

    const providerExisting = this.store.rewardLedger.get(input.providerId) ?? 0n;
    this.store.rewardLedger.set(input.providerId, providerExisting + parseAnmNanos(usage.providerRewardAnmNanos));

    const treasury = this.latestTreasurySnapshot();
    const nextAvailable = parseAnmNanos(treasury.availableAnmNanos)
      + parseAnmNanos(usage.treasuryCutAnmNanos)
      - parseAnmNanos(usage.subsidyAnmNanos);
    this.writeTreasurySnapshot({
      availableAnmNanos: formatAnmNanos(nextAvailable),
      allocatedSubsidyAnmNanos: formatAnmNanos(
        parseAnmNanos(treasury.allocatedSubsidyAnmNanos) + parseAnmNanos(usage.subsidyAnmNanos)
      ),
      paidProviderAnmNanos: formatAnmNanos(
        parseAnmNanos(treasury.paidProviderAnmNanos) + parseAnmNanos(usage.providerRewardAnmNanos)
      ),
      protocolFeesAnmNanos: formatAnmNanos(
        parseAnmNanos(treasury.protocolFeesAnmNanos) + parseAnmNanos(usage.treasuryCutAnmNanos)
      )
    });

    return { usage, settlement };
  }

  private createUser(input: {
    email: string;
    password: string;
    role: AccountUser['role'];
    oauthProvider?: string;
  }): AccountUser {
    const email = normalizeEmail(input.email);
    if (this.store.usersByEmail.has(email)) {
      throw new Error('Email already registered');
    }

    const user: AccountUser = {
      id: createId('user'),
      email,
      passwordHash: makePasswordHash(input.password),
      role: input.role,
      createdAt: nowIso(),
      oauthProvider: input.oauthProvider
    };

    this.store.users.set(user.id, user);
    this.store.usersByEmail.set(email, user.id);
    return user;
  }

  issueSession(user: AccountUser): string {
    const token = jwt.sign(
      {
        uid: user.id,
        role: user.role
      } satisfies SessionClaims,
      this.config.AICF_API_JWT_SECRET,
      { expiresIn: '12h' }
    );
    this.store.sessions.set(token, { userId: user.id, createdAt: nowIso() });
    return token;
  }

  authenticateSession(token: string): AccountUser {
    const claims = jwt.verify(token, this.config.AICF_API_JWT_SECRET) as SessionClaims;
    const session = this.store.sessions.get(token);
    if (!session || session.userId !== claims.uid) {
      throw new Error('Session expired');
    }
    const user = this.store.users.get(claims.uid);
    if (!user) {
      throw new Error('User not found');
    }
    return user;
  }

  signup(input: { email: string; password: string; role?: 'developer' | 'provider' }): { user: AuthUser; token: string } {
    const role = input.role ?? 'developer';
    const user = this.createUser({
      email: input.email,
      password: input.password,
      role
    });
    const token = this.issueSession(user);
    this.audit(user.id, user.role, 'signup', 'user', user.id, {});
    return { user: sanitizeUser(user), token };
  }

  login(input: { email: string; password: string }): { user: AuthUser; token: string } {
    const email = normalizeEmail(input.email);
    const userId = this.store.usersByEmail.get(email);
    if (!userId) {
      throw new Error('Invalid credentials');
    }
    const user = this.store.users.get(userId);
    if (!user || !checkPassword(input.password, user.passwordHash)) {
      throw new Error('Invalid credentials');
    }
    const token = this.issueSession(user);
    this.audit(user.id, user.role, 'login', 'user', user.id, {});
    return { user: sanitizeUser(user), token };
  }

  oauthStart(provider: string): { redirectUrl: string } {
    return {
      redirectUrl: `https://auth.animica.org/oauth/${provider}?scope=openid+email+profile`
    };
  }

  oauthCallback(input: {
    provider: string;
    email: string;
    oauthSubject: string;
  }): { user: AuthUser; token: string } {
    const email = normalizeEmail(input.email);
    let userId = this.store.usersByEmail.get(email);
    let user: AccountUser;
    if (!userId) {
      user = this.createUser({
        email,
        password: randomSecret('oauth-local-password'),
        role: 'developer',
        oauthProvider: input.provider
      });
    } else {
      const existing = this.store.users.get(userId);
      if (!existing) {
        throw new Error('Corrupted user mapping');
      }
      user = existing;
    }
    const token = this.issueSession(user);
    this.audit(user.id, user.role, 'oauth_login', 'user', user.id, {
      provider: input.provider,
      oauthSubject: input.oauthSubject
    });
    return { user: sanitizeUser(user), token };
  }

  listModels(): ModelDefinition[] {
    return [...this.store.models.values()].sort((a, b) => a.name.localeCompare(b.name));
  }

  listProjects(user: AccountUser): Project[] {
    const projects = [...this.store.projects.values()].filter((project) => {
      if (user.role === 'admin') return true;
      return project.ownerUserId === user.id;
    });
    return projects.map(cloneProject).sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  }

  createProject(user: AccountUser, input: { name: string; slug: string; description?: string }): Project {
    const slug = input.slug.trim().toLowerCase();
    if ([...this.store.projects.values()].some((project) => project.slug === slug)) {
      throw new Error('Project slug already exists');
    }

    const project: Project = {
      id: createId('proj'),
      ownerUserId: user.id,
      name: input.name,
      slug,
      description: input.description?.trim() ?? '',
      createdAt: nowIso(),
      updatedAt: nowIso(),
      balance: {
        availableAnm: '0',
        reservedAnm: '0',
        totalDepositedAnm: '0',
        totalSpentAnm: '0',
        totalRefundedAnm: '0'
      }
    };

    this.store.projects.set(project.id, project);
    this.audit(user.id, user.role, 'project_create', 'project', project.id, {
      slug
    });
    return cloneProject(project);
  }

  updateProject(user: AccountUser, projectId: string, input: { description?: string; webhookUrl?: string }): Project {
    const project = this.getProject(projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    if (typeof input.description === 'string') {
      project.description = input.description;
    }
    if (typeof input.webhookUrl === 'string') {
      project.webhookUrl = input.webhookUrl;
    }
    project.updatedAt = nowIso();

    this.audit(user.id, user.role, 'project_update', 'project', project.id, {
      webhookUrl: project.webhookUrl
    });
    return cloneProject(project);
  }

  linkWallet(user: AccountUser, input: { address: string; chainId: number; signature: string }): AuthUser {
    const mutable = this.store.users.get(user.id);
    if (!mutable) {
      throw new Error('User not found');
    }
    mutable.wallet = {
      address: input.address,
      chainId: input.chainId,
      signature: input.signature,
      linkedAt: nowIso()
    };

    this.audit(user.id, user.role, 'wallet_link', 'user', user.id, {
      address: input.address,
      chainId: input.chainId
    });
    return sanitizeUser(mutable);
  }

  createFundingIntent(user: AccountUser, input: {
    projectId: string;
    amountAnm: string;
    txHash?: string;
  }): {
    project: Project;
    contractCall: {
      contractAddress: string;
      method: string;
      args: Record<string, unknown>;
    };
  } {
    const project = this.getProject(input.projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    const amount = parseAnmNanos(input.amountAnm);
    if (amount <= 0n) {
      throw new Error('Funding amount must be positive');
    }

    this.adjustProjectBalance(project, {
      availableAnmNanos: amount,
      depositedAnmNanos: amount
    });

    this.audit(user.id, user.role, 'project_fund', 'project', project.id, {
      amountAnmNanos: amount.toString(),
      txHash: input.txHash
    });

    return {
      project: cloneProject(project),
      contractCall: {
        contractAddress: this.config.AICF_PROJECT_BALANCE_CONTRACT,
        method: 'deposit_project',
        args: {
          project_id: project.id,
          amount_anm_nanos: amount.toString()
        }
      }
    };
  }

  withdrawProjectBalance(user: AccountUser, input: {
    projectId: string;
    amountAnmNanos: string;
  }): Project {
    const project = this.getProject(input.projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    const amount = parseAnmNanos(input.amountAnmNanos);
    this.requireProjectFunds(project, amount);
    this.adjustProjectBalance(project, {
      availableAnmNanos: -amount,
      refundedAnmNanos: amount
    });

    this.audit(user.id, user.role, 'project_withdraw', 'project', project.id, {
      amountAnmNanos: amount.toString()
    });
    return cloneProject(project);
  }

  createApiKey(user: AccountUser, input: {
    projectId: string;
    name: string;
    scopes: ApiKeyScope[];
  }): { key: ApiKeyRecord; token: string } {
    const project = this.getProject(input.projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    const token = randomSecret('aicf_sk');
    const prefix = token.slice(0, 14);
    const key: ApiKeyRecord = {
      id: createId('key'),
      projectId: project.id,
      name: input.name,
      prefix,
      hash: hashSecret(token),
      scopes: input.scopes,
      createdAt: nowIso()
    };

    this.store.apiKeys.set(key.id, key);
    this.store.apiKeysByPrefix.set(prefix, key.id);

    this.audit(user.id, user.role, 'api_key_create', 'api_key', key.id, {
      projectId: project.id,
      scopes: key.scopes
    });

    return { key, token };
  }

  listApiKeys(user: AccountUser, projectId: string): ApiKeyRecord[] {
    const project = this.getProject(projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    return [...this.store.apiKeys.values()]
      .filter((key) => key.projectId === projectId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  revokeApiKey(user: AccountUser, keyId: string): ApiKeyRecord {
    const key = this.store.apiKeys.get(keyId);
    if (!key) {
      throw new Error('API key not found');
    }
    const project = this.getProject(key.projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    key.revokedAt = nowIso();
    this.audit(user.id, user.role, 'api_key_revoke', 'api_key', key.id, {
      projectId: key.projectId
    });
    return key;
  }

  authorizeApiKey(token: string, requiredScope: ApiKeyScope): { key: ApiKeyRecord; project: Project } {
    const prefix = token.slice(0, 14);
    const keyId = this.store.apiKeysByPrefix.get(prefix);
    if (!keyId) {
      throw new Error('Invalid API key');
    }
    const key = this.store.apiKeys.get(keyId);
    if (!key || key.revokedAt) {
      throw new Error('Invalid API key');
    }
    if (hashSecret(token) !== key.hash) {
      throw new Error('Invalid API key');
    }
    if (!key.scopes.includes(requiredScope)) {
      throw new Error(`API key missing scope '${requiredScope}'`);
    }

    key.lastUsedAt = nowIso();

    const project = this.getProject(key.projectId);
    return { key, project };
  }

  runChatCompletion(input: {
    project: Project;
    apiKey?: ApiKeyRecord;
    request: ChatCompletionInput;
  }): {
    response: Record<string, unknown>;
    usage: UsageRecord;
    settlement: SettlementRecord;
    job: JobRecord;
  } {
    if (this.store.paused) {
      throw new Error('AICF is paused by governance');
    }

    const model = this.getModel(input.request.model);
    if (model.type !== 'chat') {
      throw new Error('Model does not support chat completions');
    }

    const userPrompt = input.request.messages
      .filter((msg) => msg.role === 'user')
      .map((msg) => msg.content)
      .join('\n');
    const systemPrompt = input.request.messages
      .filter((msg) => msg.role === 'system')
      .map((msg) => msg.content)
      .join('\n');

    const maxTokens = Math.max(16, Math.min(1024, input.request.max_tokens ?? 256));
    const outputContent = [
      'AICF response',
      `model=${model.name}`,
      systemPrompt ? `policy=${systemPrompt.slice(0, 80)}` : '',
      userPrompt ? `answer=${userPrompt.slice(0, 420)}` : 'answer=No user prompt provided.'
    ]
      .filter(Boolean)
      .join(' | ');

    const inputTokens = estimateTokenCount(JSON.stringify(input.request.messages));
    const outputTokens = Math.min(maxTokens, estimateTokenCount(outputContent));

    const charge = calculateCharge({
      pricing: model.pricing,
      inputTokens,
      outputTokens,
      subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    this.requireProjectFunds(input.project, charge.netChargeAnmNanos);
    this.adjustProjectBalance(input.project, {
      availableAnmNanos: -charge.netChargeAnmNanos,
      spentAnmNanos: charge.netChargeAnmNanos
    });

    const now = nowIso();
    const job: JobRecord = {
      id: createId('job'),
      projectId: input.project.id,
      apiKeyId: input.apiKey?.id,
      status: 'completed',
      attempts: 1,
      maxAttempts: 1,
      request: {
        class: 'chat_inference',
        model: model.name,
        input: {
          messages: input.request.messages
        },
        timeoutSeconds: 60,
        replication: 1,
        verificationMode: 'none',
        outputMode: 'private',
        challengeWindowSeconds: this.config.AICF_CHALLENGE_WINDOW_SECONDS
      },
      createdAt: now,
      updatedAt: now,
      assignedProviderId: 'provider_first_party',
      assignedNodeId: 'node_first_party',
      startedAt: now,
      completedAt: now,
      output: {
        content: outputContent
      },
      usage: {
        inputTokens,
        outputTokens,
        embeddingVectors: 0,
        latencyMs: 120,
        bytesIn: JSON.stringify(input.request.messages).length,
        bytesOut: outputContent.length
      },
      budget: {
        maxAnmNanos: formatAnmNanos(charge.netChargeAnmNanos),
        reservedAnmNanos: formatAnmNanos(charge.netChargeAnmNanos),
        subsidyAnmNanos: formatAnmNanos(charge.subsidyAnmNanos)
      },
      settlement: {
        status: 'queued_onchain',
        providerRewardAnmNanos: formatAnmNanos(charge.providerRewardAnmNanos),
        treasuryCutAnmNanos: formatAnmNanos(charge.treasuryCutAnmNanos),
        chargedAnmNanos: formatAnmNanos(charge.netChargeAnmNanos),
        refundedAnmNanos: '0',
        escrowId: createId('escrow'),
        settlementId: createId('settlement')
      }
    };

    this.store.jobs.set(job.id, job);

    const { usage, settlement } = this.createUsageAndSettlement({
      jobId: job.id,
      projectId: input.project.id,
      apiKeyId: input.apiKey?.id,
      modelName: model.name,
      className: 'chat_inference',
      providerId: 'provider_first_party',
      status: 'success',
      inputTokens,
      outputTokens,
      embeddingVectors: 0,
      latencyMs: 120,
      bytesIn: job.usage.bytesIn,
      bytesOut: job.usage.bytesOut,
      subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    const response = {
      id: `chatcmpl_${createId('resp')}`,
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: model.name,
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content: outputContent
          },
          finish_reason: 'stop'
        }
      ],
      usage: {
        prompt_tokens: inputTokens,
        completion_tokens: outputTokens,
        total_tokens: inputTokens + outputTokens
      },
      aicf: {
        charged_anm_nanos: usage.chargedAnmNanos,
        provider_reward_anm_nanos: usage.providerRewardAnmNanos,
        treasury_cut_anm_nanos: usage.treasuryCutAnmNanos,
        subsidy_anm_nanos: usage.subsidyAnmNanos,
        settlement_id: settlement.id
      }
    };

    return { response, usage, settlement, job };
  }

  runEmbeddings(input: {
    project: Project;
    apiKey?: ApiKeyRecord;
    request: EmbeddingInput;
  }): {
    response: Record<string, unknown>;
    usage: UsageRecord;
    settlement: SettlementRecord;
    job: JobRecord;
  } {
    if (this.store.paused) {
      throw new Error('AICF is paused by governance');
    }

    const model = this.getModel(input.request.model);
    if (model.type !== 'embedding') {
      throw new Error('Model does not support embeddings');
    }

    const data = Array.isArray(input.request.input) ? input.request.input : [input.request.input];
    const dimensions = Math.max(8, Math.min(1536, input.request.dimensions ?? 64));

    const vectors = data.map((text, idx) => ({
      object: 'embedding',
      index: idx,
      embedding: deterministicPseudoEmbedding(text, dimensions)
    }));

    const inputTokens = data.reduce((sum, current) => sum + estimateTokenCount(current), 0);
    const charge = calculateCharge({
      pricing: model.pricing,
      inputTokens,
      outputTokens: 0,
      embeddingVectors: data.length,
      subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    this.requireProjectFunds(input.project, charge.netChargeAnmNanos);
    this.adjustProjectBalance(input.project, {
      availableAnmNanos: -charge.netChargeAnmNanos,
      spentAnmNanos: charge.netChargeAnmNanos
    });

    const now = nowIso();
    const job: JobRecord = {
      id: createId('job'),
      projectId: input.project.id,
      apiKeyId: input.apiKey?.id,
      status: 'completed',
      attempts: 1,
      maxAttempts: 1,
      request: {
        class: 'embedding_generation',
        model: model.name,
        input: {
          input: data,
          dimensions
        },
        timeoutSeconds: 45,
        replication: 1,
        verificationMode: 'none',
        outputMode: 'private',
        challengeWindowSeconds: this.config.AICF_CHALLENGE_WINDOW_SECONDS
      },
      createdAt: now,
      updatedAt: now,
      assignedProviderId: 'provider_first_party',
      assignedNodeId: 'node_first_party',
      startedAt: now,
      completedAt: now,
      output: {
        dimensions,
        count: data.length
      },
      usage: {
        inputTokens,
        outputTokens: 0,
        embeddingVectors: data.length,
        latencyMs: 80,
        bytesIn: JSON.stringify(data).length,
        bytesOut: JSON.stringify(vectors).length
      },
      budget: {
        maxAnmNanos: formatAnmNanos(charge.netChargeAnmNanos),
        reservedAnmNanos: formatAnmNanos(charge.netChargeAnmNanos),
        subsidyAnmNanos: formatAnmNanos(charge.subsidyAnmNanos)
      },
      settlement: {
        status: 'queued_onchain',
        providerRewardAnmNanos: formatAnmNanos(charge.providerRewardAnmNanos),
        treasuryCutAnmNanos: formatAnmNanos(charge.treasuryCutAnmNanos),
        chargedAnmNanos: formatAnmNanos(charge.netChargeAnmNanos),
        refundedAnmNanos: '0',
        escrowId: createId('escrow'),
        settlementId: createId('settlement')
      }
    };

    this.store.jobs.set(job.id, job);

    const { usage, settlement } = this.createUsageAndSettlement({
      jobId: job.id,
      projectId: input.project.id,
      apiKeyId: input.apiKey?.id,
      modelName: model.name,
      className: 'embedding_generation',
      providerId: 'provider_first_party',
      status: 'success',
      inputTokens,
      outputTokens: 0,
      embeddingVectors: data.length,
      latencyMs: 80,
      bytesIn: job.usage.bytesIn,
      bytesOut: job.usage.bytesOut,
      subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    const response = {
      object: 'list',
      model: model.name,
      data: vectors,
      usage: {
        prompt_tokens: inputTokens,
        total_tokens: inputTokens
      },
      aicf: {
        charged_anm_nanos: usage.chargedAnmNanos,
        provider_reward_anm_nanos: usage.providerRewardAnmNanos,
        treasury_cut_anm_nanos: usage.treasuryCutAnmNanos,
        subsidy_anm_nanos: usage.subsidyAnmNanos,
        settlement_id: settlement.id
      }
    };

    return { response, usage, settlement, job };
  }

  createAsyncJob(user: AccountUser, input: {
    projectId: string;
    apiKeyId?: string;
    request: JobRequestPayload;
    maxBudgetAnmNanos: string;
    subsidyBps?: number;
  }): JobRecord {
    if (this.store.paused) {
      throw new Error('AICF is paused by governance');
    }

    const project = this.getProject(input.projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    const model = this.getModel(input.request.model);
    const expectedInputTokens = estimateTokenCount(JSON.stringify(input.request.input ?? {}));
    const expectedOutputTokens = input.request.class === 'embedding_generation' ? 0 : 256;
    const expectedEmbeddingVectors = input.request.class === 'embedding_generation' ? 1 : 0;

    const expectedCharge = calculateCharge({
      pricing: model.pricing,
      inputTokens: expectedInputTokens,
      outputTokens: expectedOutputTokens,
      embeddingVectors: expectedEmbeddingVectors,
      subsidyBps: input.subsidyBps ?? this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    const maxBudget = parseAnmNanos(input.maxBudgetAnmNanos);
    ensureBudget(maxBudget, expectedCharge.netChargeAnmNanos);
    this.requireProjectFunds(project, maxBudget);

    this.adjustProjectBalance(project, {
      availableAnmNanos: -maxBudget,
      reservedAnmNanos: maxBudget
    });

    const createdAt = nowIso();
    const job: JobRecord = {
      id: createId('job'),
      projectId: project.id,
      apiKeyId: input.apiKeyId,
      status: 'queued',
      attempts: 0,
      maxAttempts: 3,
      request: input.request,
      createdAt,
      updatedAt: createdAt,
      usage: {
        inputTokens: 0,
        outputTokens: 0,
        embeddingVectors: 0,
        latencyMs: 0,
        bytesIn: JSON.stringify(input.request.input ?? {}).length,
        bytesOut: 0
      },
      budget: {
        maxAnmNanos: maxBudget.toString(),
        reservedAnmNanos: maxBudget.toString(),
        subsidyAnmNanos: expectedCharge.subsidyAnmNanos.toString()
      },
      settlement: {
        status: 'pending',
        providerRewardAnmNanos: '0',
        treasuryCutAnmNanos: '0',
        chargedAnmNanos: '0',
        refundedAnmNanos: '0',
        escrowId: createId('escrow')
      }
    };

    this.store.jobs.set(job.id, job);

    this.audit(user.id, user.role, 'job_create', 'job', job.id, {
      projectId: job.projectId,
      class: job.request.class,
      maxBudgetAnmNanos: job.budget.maxAnmNanos
    });

    return job;
  }

  listJobs(user: AccountUser, projectId?: string): JobRecord[] {
    return [...this.store.jobs.values()]
      .filter((job) => {
        const project = this.store.projects.get(job.projectId);
        if (!project) return false;
        if (user.role === 'admin') return !projectId || project.id === projectId;
        if (project.ownerUserId !== user.id) return false;
        if (projectId && project.id !== projectId) return false;
        return true;
      })
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  listUsage(user: AccountUser, projectId?: string): UsageRecord[] {
    return [...this.store.usage.values()]
      .filter((usage) => {
        const project = this.store.projects.get(usage.projectId);
        if (!project) return false;
        if (user.role === 'admin') return !projectId || project.id === projectId;
        if (project.ownerUserId !== user.id) return false;
        if (projectId && project.id !== projectId) return false;
        return true;
      })
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  registerProvider(user: AccountUser, input: {
    walletAddress: string;
    signature: string;
    daemonPublicKey: string;
  }): { provider: ProviderProfile; daemonToken: string } {
    if (user.role !== 'provider' && user.role !== 'admin') {
      throw new Error('Only provider/admin roles can register providers');
    }

    const existing = [...this.store.providers.values()].find((provider) => provider.userId === user.id);
    if (existing) {
      throw new Error('Provider already registered for this user');
    }

    const daemonToken = randomSecret('aicf_daemon');
    const provider: ProviderProfile = {
      id: createId('provider'),
      userId: user.id,
      walletAddress: input.walletAddress,
      daemonTokenHash: hashSecret(daemonToken),
      state: 'active',
      reputation: 50,
      totalJobsCompleted: 0,
      totalJobsFailed: 0,
      stakeAnm: '0',
      slashHistory: [],
      createdAt: nowIso()
    };

    this.store.providers.set(provider.id, provider);
    this.store.providerNodesByProviderId.set(provider.id, new Set());

    this.audit(user.id, user.role, 'provider_register', 'provider', provider.id, {
      walletAddress: input.walletAddress,
      daemonPublicKey: input.daemonPublicKey
    });

    return { provider, daemonToken };
  }

  authenticateProviderDaemon(providerId: string, daemonToken: string): ProviderProfile {
    const provider = this.store.providers.get(providerId);
    if (!provider) {
      throw new Error('Provider not found');
    }
    if (hashSecret(daemonToken) !== provider.daemonTokenHash) {
      throw new Error('Invalid provider daemon token');
    }
    return provider;
  }

  providerStake(provider: ProviderProfile, amountAnmNanos: string): ProviderProfile {
    const amount = parseAnmNanos(amountAnmNanos);
    if (amount <= 0n) {
      throw new Error('Stake amount must be positive');
    }

    provider.stakeAnm = formatAnmNanos(parseAnmNanos(provider.stakeAnm) + amount);
    this.audit(provider.userId, 'provider', 'provider_stake', 'provider', provider.id, {
      amountAnmNanos: amount.toString()
    });
    return provider;
  }

  providerUnstake(provider: ProviderProfile, amountAnmNanos: string): ProviderProfile {
    const amount = parseAnmNanos(amountAnmNanos);
    const current = parseAnmNanos(provider.stakeAnm);
    const minStake = BigInt(this.config.AICF_MIN_PROVIDER_STAKE_ANM) * ANM_NANOS;

    if (amount <= 0n) {
      throw new Error('Unstake amount must be positive');
    }
    if (current - amount < minStake) {
      throw new Error('Unstake would violate minimum provider stake');
    }

    provider.stakeAnm = formatAnmNanos(current - amount);
    this.audit(provider.userId, 'provider', 'provider_unstake', 'provider', provider.id, {
      amountAnmNanos: amount.toString()
    });
    return provider;
  }

  registerProviderNode(provider: ProviderProfile, input: {
    metadata: {
      name: string;
      machineType: string;
      os: string;
      labels: string[];
    };
    capabilities: ProviderNodeCapability;
    benchmark: {
      llmTokensPerSecond: number;
      embeddingVectorsPerSecond: number;
      trainingSamplesPerSecond: number;
      score: number;
    };
  }): ProviderNode {
    const node: ProviderNode = {
      id: createId('node'),
      providerId: provider.id,
      metadata: input.metadata,
      capabilities: input.capabilities,
      benchmark: input.benchmark,
      state: 'active',
      lastHeartbeatAt: nowIso(),
      currentLoad: 0,
      queueDepth: 0
    };

    this.store.providerNodes.set(node.id, node);
    const nodes = this.store.providerNodesByProviderId.get(provider.id) ?? new Set<string>();
    nodes.add(node.id);
    this.store.providerNodesByProviderId.set(provider.id, nodes);

    this.audit(provider.userId, 'provider', 'provider_node_register', 'provider_node', node.id, {
      providerId: provider.id,
      runtime: node.capabilities.runtime
    });

    return node;
  }

  heartbeatProviderNode(provider: ProviderProfile, nodeId: string, input: {
    currentLoad: number;
    queueDepth: number;
    state?: ProviderNode['state'];
  }): ProviderNode {
    const node = this.store.providerNodes.get(nodeId);
    if (!node || node.providerId !== provider.id) {
      throw new Error('Provider node not found');
    }

    node.lastHeartbeatAt = nowIso();
    node.currentLoad = Math.max(0, Math.min(100, input.currentLoad));
    node.queueDepth = Math.max(0, input.queueDepth);
    if (input.state) {
      node.state = input.state;
    }

    return node;
  }

  schedulerTick(): {
    assigned: Array<{ jobId: string; providerId: string; nodeId: string; score: number }>;
    skipped: string[];
  } {
    if (!this.store.featureFlags.get('aicf.scheduler.enabled')?.enabled) {
      return { assigned: [], skipped: [] };
    }

    const queued = [...this.store.jobs.values()].filter((job) => job.status === 'queued');
    const providers = [...this.store.providers.values()].filter((provider) => provider.id !== 'provider_first_party');
    const nodes = [...this.store.providerNodes.values()];

    const assigned: Array<{ jobId: string; providerId: string; nodeId: string; score: number }> = [];
    const skipped: string[] = [];

    for (const job of queued) {
      const selected = chooseProvider({
        job,
        providers,
        nodes,
        minStakeAnmNanos: BigInt(this.config.AICF_MIN_PROVIDER_STAKE_ANM) * ANM_NANOS
      });

      if (!selected) {
        skipped.push(job.id);
        continue;
      }

      job.status = 'assigned';
      job.attempts += 1;
      job.assignedProviderId = selected.providerId;
      job.assignedNodeId = selected.nodeId;
      job.updatedAt = nowIso();

      const queue = this.store.providerJobQueue.get(selected.providerId) ?? [];
      queue.push(job.id);
      this.store.providerJobQueue.set(selected.providerId, queue);

      const node = this.store.providerNodes.get(selected.nodeId);
      if (node) {
        node.queueDepth += 1;
      }

      assigned.push({
        jobId: job.id,
        providerId: selected.providerId,
        nodeId: selected.nodeId,
        score: selected.score
      });
    }

    return { assigned, skipped };
  }

  runFirstPartyFallback(limit = 5): { completed: string[] } {
    const queued = [...this.store.jobs.values()]
      .filter((job) => job.status === 'queued')
      .slice(0, Math.max(0, limit));
    const completed: string[] = [];

    for (const job of queued) {
      const project = this.getProject(job.projectId);
      const model = this.getModel(job.request.model);

      const output =
        job.request.class === 'embedding_generation'
          ? {
              embeddings: [deterministicPseudoEmbedding(JSON.stringify(job.request.input ?? {}), 64)]
            }
          : {
              content: `first-party fallback execution for ${job.request.class} (${job.request.model})`
            };

      const inputTokens = estimateTokenCount(JSON.stringify(job.request.input ?? {}));
      const outputTokens = estimateTokenCount(JSON.stringify(output));
      const embeddingVectors = job.request.class === 'embedding_generation' ? 1 : 0;

      const charge = calculateCharge({
        pricing: model.pricing,
        inputTokens,
        outputTokens,
        embeddingVectors,
        subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
      });

      const reserved = parseAnmNanos(job.budget.reservedAnmNanos);
      const finalCharge = charge.netChargeAnmNanos > reserved ? reserved : charge.netChargeAnmNanos;
      const refund = reserved - finalCharge;

      this.adjustProjectBalance(project, {
        reservedAnmNanos: -reserved,
        spentAnmNanos: finalCharge,
        availableAnmNanos: refund,
        refundedAnmNanos: refund
      });

      const usage = this.createUsageAndSettlement({
        jobId: job.id,
        projectId: job.projectId,
        apiKeyId: job.apiKeyId,
        modelName: job.request.model,
        className: job.request.class,
        providerId: 'provider_first_party',
        status: 'success',
        inputTokens,
        outputTokens,
        embeddingVectors,
        latencyMs: 160,
        bytesIn: job.usage.bytesIn,
        bytesOut: JSON.stringify(output).length,
        subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
      });

      job.status = 'completed';
      job.assignedProviderId = 'provider_first_party';
      job.assignedNodeId = 'node_first_party';
      job.output = output;
      job.updatedAt = nowIso();
      job.startedAt = job.startedAt ?? nowIso();
      job.completedAt = nowIso();
      job.usage = {
        inputTokens,
        outputTokens,
        embeddingVectors,
        latencyMs: 160,
        bytesIn: job.usage.bytesIn,
        bytesOut: JSON.stringify(output).length
      };
      job.settlement = {
        status: usage.settlement.status,
        providerRewardAnmNanos: usage.usage.providerRewardAnmNanos,
        treasuryCutAnmNanos: usage.usage.treasuryCutAnmNanos,
        chargedAnmNanos: finalCharge.toString(),
        refundedAnmNanos: refund.toString(),
        escrowId: job.settlement.escrowId,
        settlementId: usage.settlement.id
      };

      completed.push(job.id);
    }

    return { completed };
  }

  providerClaimJobs(provider: ProviderProfile, nodeId: string, limit = 10): JobRecord[] {
    const node = this.store.providerNodes.get(nodeId);
    if (!node || node.providerId !== provider.id) {
      throw new Error('Provider node not found');
    }

    const queue = this.store.providerJobQueue.get(provider.id) ?? [];
    const claimed: JobRecord[] = [];

    while (queue.length > 0 && claimed.length < limit) {
      const jobId = queue.shift();
      if (!jobId) {
        break;
      }
      const job = this.store.jobs.get(jobId);
      if (!job || job.status !== 'assigned' || job.assignedNodeId !== nodeId) {
        continue;
      }
      job.status = 'running';
      job.startedAt = nowIso();
      job.updatedAt = nowIso();
      claimed.push(job);
    }

    this.store.providerJobQueue.set(provider.id, queue);
    node.currentLoad = Math.min(100, node.currentLoad + claimed.length * 10);
    node.queueDepth = Math.max(0, node.queueDepth - claimed.length);

    return claimed;
  }

  providerSubmitResult(provider: ProviderProfile, nodeId: string, jobId: string, submission: ProviderRuntimeSubmission): JobRecord {
    const job = this.store.jobs.get(jobId);
    if (!job) {
      throw new Error('Job not found');
    }
    if (job.assignedProviderId !== provider.id || job.assignedNodeId !== nodeId) {
      throw new Error('Job is not assigned to this provider node');
    }
    if (job.status !== 'running' && job.status !== 'assigned') {
      throw new Error('Job is not in executable state');
    }

    const model = this.getModel(job.request.model);
    const inputTokens = submission.usage?.inputTokens ?? estimateTokenCount(JSON.stringify(job.request.input));
    const outputTokens = submission.usage?.outputTokens ?? estimateTokenCount(JSON.stringify(submission.output));
    const embeddingVectors = submission.usage?.embeddingVectors ?? (job.request.class === 'embedding_generation' ? 1 : 0);

    const charge = calculateCharge({
      pricing: model.pricing,
      inputTokens,
      outputTokens,
      embeddingVectors,
      subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    let finalCharge = charge.netChargeAnmNanos;
    const reserved = parseAnmNanos(job.budget.reservedAnmNanos);
    if (finalCharge > reserved) {
      finalCharge = reserved;
    }
    const refund = reserved - finalCharge;

    const project = this.getProject(job.projectId);
    this.adjustProjectBalance(project, {
      reservedAnmNanos: -reserved,
      spentAnmNanos: finalCharge,
      availableAnmNanos: refund,
      refundedAnmNanos: refund
    });

    const usageLatency = submission.usage?.latencyMs ?? Math.max(100, Math.round(Math.random() * 600));
    const usageBytesOut = submission.usage?.bytesOut ?? JSON.stringify(submission.output).length;

    const { usage, settlement } = this.createUsageAndSettlement({
      jobId: job.id,
      projectId: job.projectId,
      apiKeyId: job.apiKeyId,
      modelName: job.request.model,
      className: job.request.class,
      providerId: provider.id,
      status: 'success',
      inputTokens,
      outputTokens,
      embeddingVectors,
      latencyMs: usageLatency,
      bytesIn: job.usage.bytesIn,
      bytesOut: usageBytesOut,
      subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    job.status = 'completed';
    job.updatedAt = nowIso();
    job.completedAt = nowIso();
    job.output = submission.output;
    job.usage = {
      inputTokens,
      outputTokens,
      embeddingVectors,
      latencyMs: usageLatency,
      bytesIn: job.usage.bytesIn,
      bytesOut: usageBytesOut
    };
    job.settlement = {
      status: settlement.status,
      providerRewardAnmNanos: usage.providerRewardAnmNanos,
      treasuryCutAnmNanos: usage.treasuryCutAnmNanos,
      chargedAnmNanos: finalCharge.toString(),
      refundedAnmNanos: refund.toString(),
      escrowId: job.settlement.escrowId,
      settlementId: settlement.id
    };

    const providerMutable = this.store.providers.get(provider.id);
    if (providerMutable) {
      providerMutable.totalJobsCompleted += 1;
      providerMutable.reputation = Math.min(100, providerMutable.reputation + 1);
    }

    const node = this.store.providerNodes.get(nodeId);
    if (node) {
      node.currentLoad = Math.max(0, node.currentLoad - 10);
    }

    return job;
  }

  providerFailJob(provider: ProviderProfile, nodeId: string, jobId: string, reason: string): JobRecord {
    const job = this.store.jobs.get(jobId);
    if (!job) {
      throw new Error('Job not found');
    }
    if (job.assignedProviderId !== provider.id || job.assignedNodeId !== nodeId) {
      throw new Error('Job is not assigned to this provider node');
    }

    const providerMutable = this.store.providers.get(provider.id);
    if (providerMutable) {
      providerMutable.totalJobsFailed += 1;
      providerMutable.reputation = Math.max(0, providerMutable.reputation - 2);
    }

    if (job.attempts < job.maxAttempts) {
      job.status = 'queued';
      job.updatedAt = nowIso();
      job.assignedProviderId = undefined;
      job.assignedNodeId = undefined;
      job.error = `retryable:${reason}`;
      return job;
    }

    const reserved = parseAnmNanos(job.budget.reservedAnmNanos);
    const project = this.getProject(job.projectId);
    this.adjustProjectBalance(project, {
      reservedAnmNanos: -reserved,
      availableAnmNanos: reserved,
      refundedAnmNanos: reserved
    });

    job.status = 'failed';
    job.error = reason;
    job.failedAt = nowIso();
    job.updatedAt = nowIso();
    job.settlement.refundedAnmNanos = reserved.toString();

    return job;
  }

  listProviderJobs(provider: ProviderProfile): JobRecord[] {
    return [...this.store.jobs.values()]
      .filter((job) => job.assignedProviderId === provider.id)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  listProviderContractJobs(provider: ProviderProfile): ContractJobRecord[] {
    return [...this.store.contractJobs.values()]
      .filter((job) => job.assignedProviderIds.includes(provider.id))
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((job) => cloneDeep(job));
  }

  providerRewardBalance(providerId: string): string {
    return (this.store.rewardLedger.get(providerId) ?? 0n).toString();
  }

  providerClaimRewards(provider: ProviderProfile): {
    claimedAnmNanos: string;
    contractCall: {
      contractAddress: string;
      method: string;
      args: Record<string, unknown>;
    };
  } {
    const current = this.store.rewardLedger.get(provider.id) ?? 0n;
    if (current <= 0n) {
      throw new Error('No ANM rewards available to claim');
    }

    this.store.rewardLedger.set(provider.id, 0n);
    this.audit(provider.userId, 'provider', 'provider_claim_rewards', 'provider', provider.id, {
      amountAnmNanos: current.toString()
    });

    return {
      claimedAnmNanos: current.toString(),
      contractCall: {
        contractAddress: this.config.AICF_REWARDS_CONTRACT,
        method: 'claim_rewards',
        args: {
          provider_id: provider.id,
          amount_anm_nanos: current.toString()
        }
      }
    };
  }

  openDispute(user: AccountUser, input: { jobId: string; reason: string }): JobRecord {
    if (!this.store.featureFlags.get('aicf.disputes.enabled')?.enabled) {
      throw new Error('Disputes are disabled by governance');
    }

    const job = this.store.jobs.get(input.jobId);
    if (!job) {
      throw new Error('Job not found');
    }
    const project = this.getProject(job.projectId);
    if (project.ownerUserId !== user.id && user.role !== 'admin') {
      throw new Error('Forbidden');
    }

    job.status = 'disputed';
    job.updatedAt = nowIso();
    job.error = input.reason;

    this.audit(user.id, user.role, 'dispute_open', 'job', job.id, {
      reason: input.reason
    });

    return job;
  }

  resolveDispute(admin: AccountUser, input: {
    jobId: string;
    action: 'uphold_provider' | 'slash_provider';
    slashAmountAnmNanos?: string;
    note?: string;
  }): JobRecord {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }
    const job = this.store.jobs.get(input.jobId);
    if (!job) {
      throw new Error('Job not found');
    }
    if (job.status !== 'disputed') {
      throw new Error('Job is not disputed');
    }

    if (input.action === 'slash_provider' && job.assignedProviderId) {
      const provider = this.store.providers.get(job.assignedProviderId);
      if (provider) {
        const slashAmount = parseAnmNanos(input.slashAmountAnmNanos ?? '0');
        const stake = parseAnmNanos(provider.stakeAnm);
        const finalSlash = slashAmount > stake ? stake : slashAmount;
        provider.stakeAnm = formatAnmNanos(stake - finalSlash);
        provider.slashHistory.push({
          id: createId('slash'),
          amountAnm: finalSlash.toString(),
          reason: input.note ?? 'dispute_slash',
          createdAt: nowIso()
        });
        provider.state = provider.reputation < 20 ? 'quarantined' : provider.state;
      }
    }

    job.status = 'completed';
    job.updatedAt = nowIso();

    this.audit(admin.id, admin.role, 'dispute_resolve', 'job', job.id, {
      action: input.action,
      note: input.note
    });

    return job;
  }

  listProviders(requester: AccountUser): ProviderProfile[] {
    if (requester.role !== 'admin') {
      return [...this.store.providers.values()].filter((provider) => provider.userId === requester.id);
    }
    return [...this.store.providers.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  setProviderState(admin: AccountUser, providerId: string, state: ProviderProfile['state'], note?: string): ProviderProfile {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }
    const provider = this.store.providers.get(providerId);
    if (!provider) {
      throw new Error('Provider not found');
    }
    provider.state = state;
    this.audit(admin.id, admin.role, 'provider_state_update', 'provider', providerId, {
      state,
      note
    });
    return provider;
  }

  listProviderNodes(requester: AccountUser, providerId?: string): ProviderNode[] {
    return [...this.store.providerNodes.values()]
      .filter((node) => {
        if (requester.role !== 'admin') {
          const provider = [...this.store.providers.values()].find((p) => p.userId === requester.id);
          if (!provider || provider.id !== node.providerId) {
            return false;
          }
        }
        if (providerId && node.providerId !== providerId) {
          return false;
        }
        return true;
      })
      .sort((a, b) => b.lastHeartbeatAt.localeCompare(a.lastHeartbeatAt));
  }

  listTreasurySnapshot(): TreasurySnapshot {
    return this.latestTreasurySnapshot();
  }

  depositTreasury(
    admin: AccountUser,
    input: {
      amountAnmNanos: string;
      sourceTxHash?: string;
      note?: string;
    }
  ): {
    treasury: TreasurySnapshot;
    contractCall: {
      contractAddress: string;
      method: string;
      args: Record<string, unknown>;
    };
  } {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }

    const amount = parseAnmNanos(input.amountAnmNanos);
    if (amount <= 0n) {
      throw new Error('Deposit amount must be positive');
    }

    const current = this.latestTreasurySnapshot();
    const next = this.writeTreasurySnapshot({
      availableAnmNanos: formatAnmNanos(parseAnmNanos(current.availableAnmNanos) + amount)
    });

    this.audit(admin.id, admin.role, 'treasury_deposit', 'treasury', next.id, {
      amountAnmNanos: amount.toString(),
      sourceTxHash: input.sourceTxHash,
      note: input.note
    });

    return {
      treasury: cloneDeep(next),
      contractCall: {
        contractAddress: this.config.AICF_TREASURY_ADDRESS,
        method: 'deposit_treasury',
        args: {
          amountAnmNanos: amount.toString(),
          sourceTxHash: input.sourceTxHash
        }
      }
    };
  }

  allocateGrant(admin: AccountUser, input: {
    projectId: string;
    amountAnmNanos: string;
    reason: string;
    expiresAt?: string;
  }): { grantId: string; project: Project } {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }

    const amount = parseAnmNanos(input.amountAnmNanos);
    if (amount <= 0n) {
      throw new Error('Grant amount must be positive');
    }

    const treasury = this.latestTreasurySnapshot();
    if (parseAnmNanos(treasury.availableAnmNanos) < amount) {
      throw new Error('Treasury has insufficient ANM');
    }

    const project = this.getProject(input.projectId);
    this.adjustProjectBalance(project, {
      availableAnmNanos: amount,
      depositedAnmNanos: amount
    });

    const grantId = createId('grant');
    this.store.grants.set(grantId, {
      id: grantId,
      projectId: project.id,
      amountAnmNanos: amount.toString(),
      consumedAnmNanos: '0',
      reason: input.reason,
      createdAt: nowIso(),
      expiresAt: input.expiresAt
    });

    this.writeTreasurySnapshot({
      availableAnmNanos: formatAnmNanos(parseAnmNanos(treasury.availableAnmNanos) - amount),
      grantsOutstandingAnmNanos: formatAnmNanos(
        parseAnmNanos(treasury.grantsOutstandingAnmNanos) + amount
      )
    });

    this.audit(admin.id, admin.role, 'treasury_grant_allocate', 'project', project.id, {
      amountAnmNanos: amount.toString(),
      reason: input.reason
    });

    return { grantId, project: cloneProject(project) };
  }

  setFeatureFlag(admin: AccountUser, input: { key: string; enabled: boolean; note?: string }): FeatureFlag {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }

    const flag: FeatureFlag = {
      key: input.key,
      enabled: input.enabled,
      note: input.note
    };
    this.store.featureFlags.set(flag.key, flag);

    this.audit(admin.id, admin.role, 'feature_flag_update', 'feature_flag', flag.key, {
      enabled: flag.enabled,
      note: flag.note
    });

    if (flag.key === 'aicf.admin.pause_enabled' && !flag.enabled) {
      this.store.paused = false;
    }

    return flag;
  }

  listFeatureFlags(): FeatureFlag[] {
    return [...this.store.featureFlags.values()].sort((a, b) => a.key.localeCompare(b.key));
  }

  listGrants(user: AccountUser) {
    if (user.role === 'admin') {
      return [...this.store.grants.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    }
    const ownedProjectIds = new Set(
      [...this.store.projects.values()].filter((project) => project.ownerUserId === user.id).map((project) => project.id)
    );
    return [...this.store.grants.values()].filter((grant) => ownedProjectIds.has(grant.projectId));
  }

  pauseAll(admin: AccountUser, paused: boolean, reason?: string): void {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }
    if (!this.store.featureFlags.get('aicf.admin.pause_enabled')?.enabled) {
      throw new Error('Pause control is disabled');
    }

    this.store.paused = paused;
    this.audit(admin.id, admin.role, paused ? 'platform_pause' : 'platform_resume', 'system', 'aicf', {
      reason
    });
  }

  listAuditLogs(admin: AccountUser): AuditLogEntry[] {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }
    return [...this.store.auditLogs.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  listSettlements(user: AccountUser, projectId?: string): SettlementRecord[] {
    return [...this.store.settlements.values()]
      .filter((settlement) => {
        const project = this.store.projects.get(settlement.projectId);
        if (!project) return false;
        if (user.role === 'admin') {
          return !projectId || project.id === projectId;
        }
        if (project.ownerUserId !== user.id) return false;
        return !projectId || project.id === projectId;
      })
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  createProviderContractCalls(provider: ProviderProfile): {
    stake: { contractAddress: string; method: string; args: Record<string, unknown> };
    register: { contractAddress: string; method: string; args: Record<string, unknown> };
  } {
    return {
      stake: {
        contractAddress: this.config.AICF_STAKE_MANAGER_CONTRACT,
        method: 'stake_for_provider',
        args: {
          provider_id: provider.id,
          wallet: provider.walletAddress
        }
      },
      register: {
        contractAddress: this.config.AICF_PROVIDER_REGISTRY_CONTRACT,
        method: 'register_provider',
        args: {
          provider_id: provider.id,
          wallet: provider.walletAddress
        }
      }
    };
  }

  getGovernanceConfig() {
    return {
      chainId: this.config.AICF_CHAIN_ID,
      treasuryAddress: this.config.AICF_TREASURY_ADDRESS,
      governanceAddress: this.config.AICF_GOVERNANCE_ADDRESS,
      projectBalanceAddress: this.config.AICF_PROJECT_BALANCE_CONTRACT,
      jobEscrowAddress: this.config.AICF_JOB_ESCROW_CONTRACT,
      rewardsAddress: this.config.AICF_REWARDS_CONTRACT,
      providerRegistryAddress: this.config.AICF_PROVIDER_REGISTRY_CONTRACT,
      stakeManagerAddress: this.config.AICF_STAKE_MANAGER_CONTRACT,
      disputeManagerAddress: this.config.AICF_DISPUTE_MANAGER_CONTRACT,
      minProviderStakeAnmNanos: (BigInt(this.config.AICF_MIN_PROVIDER_STAKE_ANM) * ANM_NANOS).toString(),
      challengeWindowSeconds: this.config.AICF_CHALLENGE_WINDOW_SECONDS,
      paused: this.store.paused
    };
  }

  private mustHaveContractJobsEnabled(): void {
    if (!this.store.featureFlags.get('aicf.contract_jobs.enabled')?.enabled) {
      throw new Error('Contract jobs are disabled by governance');
    }
  }

  private resolveContractByAddress(address: string): ContractRecord {
    const normalized = address.trim().toLowerCase();
    const id = this.store.contractsByAddress.get(normalized);
    if (!id) {
      throw new Error('Contract not found');
    }
    const contract = this.store.contracts.get(id);
    if (!contract) {
      throw new Error('Contract mapping is corrupted');
    }
    return contract;
  }

  private contractArtifactRef(artifactId: string): string {
    return `aicf://contracts/artifacts/${artifactId}`;
  }

  private parseContractArtifactRef(ref: string): string {
    const prefix = 'aicf://contracts/artifacts/';
    if (!ref.startsWith(prefix)) {
      throw new Error('Invalid artifact reference');
    }
    const id = ref.slice(prefix.length).trim();
    if (!id) {
      throw new Error('Invalid artifact reference');
    }
    return id;
  }

  private resolveContractJob(jobId: string): ContractJobRecord {
    const job = this.store.contractJobs.get(jobId);
    if (!job) {
      throw new Error('Contract job not found');
    }
    return job;
  }

  private resolveAgentTask(taskId: string): AgentTaskRecord {
    const task = this.store.agentTasks.get(taskId);
    if (!task) {
      throw new Error('Agent task not found');
    }
    return task;
  }

  private hasContractAccess(user: AccountUser, contract: ContractRecord): boolean {
    if (user.role === 'admin') return true;
    return contract.ownerUserId === user.id;
  }

  private assertContractAccess(user: AccountUser, contract: ContractRecord): void {
    if (!this.hasContractAccess(user, contract)) {
      throw new Error('Forbidden');
    }
  }

  private resolveContractForJob(job: ContractJobRecord): ContractRecord {
    return this.resolveContractByAddress(job.contractAddress);
  }

  private assertContractJobAccess(user: AccountUser, job: ContractJobRecord): void {
    if (user.role === 'admin') return;
    const contract = this.resolveContractForJob(job);
    if (
      contract.ownerUserId === user.id ||
      job.payer.toLowerCase() === (user.wallet?.address ?? '').toLowerCase() ||
      job.requester.toLowerCase() === (user.wallet?.address ?? '').toLowerCase()
    ) {
      return;
    }
    throw new Error('Forbidden');
  }

  private pushChainEvent(
    eventType: ContractJobChainEvent['eventType'],
    input: {
      contractAddress: string;
      onchainJobId?: string;
      onchainTaskId?: string;
      payload: Record<string, unknown>;
    }
  ): ContractJobChainEvent {
    const event: ContractJobChainEvent = {
      id: createId('chain_evt'),
      eventType,
      contractAddress: input.contractAddress,
      onchainJobId: input.onchainJobId,
      onchainTaskId: input.onchainTaskId,
      payload: input.payload,
      observedAt: nowIso()
    };
    this.store.chainEvents.set(event.id, event);
    this.store.chainEventQueue.push(event.id);
    return event;
  }

  private appendEscrowEvent(input: {
    jobId: string;
    type: EscrowEventRecord['type'];
    amountAnmNanos: string;
    actor: string;
    txHash?: string;
    metadata?: Record<string, unknown>;
  }): EscrowEventRecord {
    const event: EscrowEventRecord = {
      id: createId('escrow_evt'),
      jobId: input.jobId,
      type: input.type,
      amountAnmNanos: input.amountAnmNanos,
      actor: input.actor,
      txHash: input.txHash,
      metadata: input.metadata,
      createdAt: nowIso()
    };
    this.store.escrowEvents.set(event.id, event);
    return event;
  }

  private toFutureIso(secondsFromNow: number): string {
    const now = Date.now();
    const ms = Math.max(1, Math.trunc(secondsFromNow)) * 1000;
    return new Date(now + ms).toISOString();
  }

  private isPast(iso: string): boolean {
    return Date.now() > new Date(iso).getTime();
  }

  private providerAllowedByPolicy(job: ContractJobRecord, provider: ProviderProfile): boolean {
    const policy = job.providerPolicy;
    if (policy.mode === 'open') return true;
    if (policy.mode === 'allowlist') return policy.providerIds.includes(provider.id);
    if (policy.mode === 'blocklist') return !policy.providerIds.includes(provider.id);
    return true;
  }

  listContracts(user: AccountUser): ContractRecord[] {
    const contracts = [...this.store.contracts.values()].filter((contract) => {
      if (user.role === 'admin') return true;
      return contract.ownerUserId === user.id;
    });
    contracts.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return contracts.map((contract) => cloneDeep(contract));
  }

  registerContract(
    user: AccountUser,
    input: {
      address: string;
      type: ContractRecord['type'];
      metadata: ContractRecord['metadata'];
    }
  ): ContractRecord {
    this.mustHaveContractJobsEnabled();
    const normalized = input.address.trim().toLowerCase();
    if (!/^anm1[0-9a-z]{8,}$/.test(normalized)) {
      throw new Error('Invalid contract address');
    }
    const existingId = this.store.contractsByAddress.get(normalized);
    if (existingId) {
      const existing = this.store.contracts.get(existingId);
      if (!existing) throw new Error('Corrupted contract record');
      if (user.role !== 'admin' && existing.ownerUserId !== user.id) {
        throw new Error('Contract already registered by another owner');
      }
      existing.type = input.type;
      existing.metadata = input.metadata;
      existing.updatedAt = nowIso();
      return cloneDeep(existing);
    }

    const contract: ContractRecord = {
      id: createId('contract'),
      address: normalized,
      ownerUserId: user.id,
      type: input.type,
      metadata: input.metadata,
      paused: false,
      createdAt: nowIso(),
      updatedAt: nowIso()
    };
    this.store.contracts.set(contract.id, contract);
    this.store.contractsByAddress.set(contract.address, contract.id);

    this.audit(user.id, user.role, 'contract_register', 'contract', contract.address, {
      type: contract.type,
      metadata: contract.metadata
    });
    return cloneDeep(contract);
  }

  setContractPaused(user: AccountUser, contractAddress: string, paused: boolean): ContractRecord {
    const contract = this.resolveContractByAddress(contractAddress);
    this.assertContractAccess(user, contract);
    contract.paused = paused;
    contract.updatedAt = nowIso();
    this.audit(user.id, user.role, paused ? 'contract_pause' : 'contract_resume', 'contract', contract.address, {});
    return cloneDeep(contract);
  }

  getContract(user: AccountUser, contractAddress: string): ContractRecord {
    const contract = this.resolveContractByAddress(contractAddress);
    this.assertContractAccess(user, contract);
    return cloneDeep(contract);
  }

  upsertContractArtifacts(
    user: AccountUser,
    contractAddress: string,
    input: {
      sourceCode?: string;
      sourceLanguage?: string;
      abiJson?: string;
    }
  ): {
    contract: ContractRecord;
    artifacts: ContractArtifactRecord[];
  } {
    const contract = this.resolveContractByAddress(contractAddress);
    this.assertContractAccess(user, contract);

    const sourceCode = input.sourceCode?.trim();
    const abiJson = input.abiJson?.trim();
    if (!sourceCode && !abiJson) {
      throw new Error('No artifact content provided');
    }

    const created: ContractArtifactRecord[] = [];
    const metadata = { ...contract.metadata };

    if (sourceCode) {
      const artifact: ContractArtifactRecord = {
        id: createId('cartifact'),
        contractAddress: contract.address,
        kind: 'source',
        language: input.sourceLanguage?.trim() || 'vmpy',
        content: sourceCode,
        createdBy: user.id,
        createdAt: nowIso()
      };
      this.store.contractArtifacts.set(artifact.id, artifact);
      metadata.sourceRef = this.contractArtifactRef(artifact.id);
      created.push(artifact);
    }

    if (abiJson) {
      const artifact: ContractArtifactRecord = {
        id: createId('cartifact'),
        contractAddress: contract.address,
        kind: 'abi',
        language: 'json',
        content: abiJson,
        createdBy: user.id,
        createdAt: nowIso()
      };
      this.store.contractArtifacts.set(artifact.id, artifact);
      metadata.abiRef = this.contractArtifactRef(artifact.id);
      created.push(artifact);
    }

    contract.metadata = metadata;
    contract.updatedAt = nowIso();

    this.audit(user.id, user.role, 'contract_artifacts_upsert', 'contract', contract.address, {
      createdArtifactIds: created.map((artifact) => artifact.id),
      sourceRef: contract.metadata.sourceRef,
      abiRef: contract.metadata.abiRef
    });

    return {
      contract: cloneDeep(contract),
      artifacts: created.map((artifact) => cloneDeep(artifact))
    };
  }

  getContractArtifactByRef(user: AccountUser, ref: string): ContractArtifactRecord {
    const artifactId = this.parseContractArtifactRef(ref);
    const artifact = this.store.contractArtifacts.get(artifactId);
    if (!artifact) {
      throw new Error('Artifact not found');
    }
    const contract = this.resolveContractByAddress(artifact.contractAddress);
    this.assertContractAccess(user, contract);
    return cloneDeep(artifact);
  }

  createContractJob(
    user: AccountUser,
    input: ContractJobRequest & {
      onchainJobId?: string;
      txHash?: string;
    }
  ): {
    job: ContractJobRecord;
    escrowEvents: EscrowEventRecord[];
    contractCalls: {
      approve: { contractAddress: string; method: string; args: Record<string, unknown> };
      fund: { contractAddress: string; method: string; args: Record<string, unknown> };
      reserve: { contractAddress: string; method: string; args: Record<string, unknown> };
      request: { contractAddress: string; method: string; args: Record<string, unknown> };
    };
  } {
    this.mustHaveContractJobsEnabled();
    const contract = this.resolveContractByAddress(input.contractAddress);
    this.assertContractAccess(user, contract);
    if (contract.paused) {
      throw new Error('Contract is paused');
    }
    if (this.store.paused) {
      throw new Error('AICF is paused by governance');
    }

    const budget = parseAnmNanos(input.maxBudgetAnmNanos);
    if (budget <= 0n) {
      throw new Error('Invalid max ANM budget');
    }
    if (input.timeoutSeconds <= 0 || input.challengeWindowSeconds <= 0) {
      throw new Error('Invalid timeout/challenge window');
    }
    if (input.replication < 1 || input.quorum < 1 || input.quorum > input.replication) {
      throw new Error('Invalid replication/quorum settings');
    }

    const onchainJobId = input.onchainJobId ?? createId('onchain_job');
    if (this.store.contractJobsByOnchainId.has(onchainJobId)) {
      throw new Error('on-chain job id already exists');
    }

    const state: ContractJobState = 'requested';
    const job: ContractJobRecord = {
      id: createId('cjob'),
      onchainJobId,
      contractAddress: contract.address,
      requester: input.requester,
      payer: input.payer,
      modelId: input.modelId,
      jobType: input.jobType,
      mode: input.verificationMode,
      callbackMode: input.callbackMode,
      resultType: input.resultType,
      inputRefHash: input.inputRefHash,
      outputSchemaRef: input.outputSchemaRef,
      state,
      budgetAnmNanos: budget.toString(),
      fundedAnmNanos: budget.toString(),
      reservedAnmNanos: budget.toString(),
      paidAnmNanos: '0',
      refundedAnmNanos: '0',
      timeoutAt: this.toFutureIso(input.timeoutSeconds),
      challengeWindowEndsAt: undefined,
      assignedProviderIds: [],
      acceptedResultHash: undefined,
      finalResultRef: undefined,
      disputeStatus: 'none',
      createdAt: nowIso(),
      updatedAt: nowIso(),
      providerPolicy: input.providerPolicy,
      privacy: input.privacy,
      replication: input.replication,
      quorum: input.quorum,
      metadata: input.metadata
    };

    this.store.contractJobs.set(job.id, job);
    this.store.contractJobsByOnchainId.set(onchainJobId, job.id);

    const funded = this.appendEscrowEvent({
      jobId: job.id,
      type: 'funded',
      amountAnmNanos: budget.toString(),
      actor: user.id,
      txHash: input.txHash
    });
    const reserved = this.appendEscrowEvent({
      jobId: job.id,
      type: 'reserved',
      amountAnmNanos: budget.toString(),
      actor: 'aicf_escrow',
      metadata: { onchainJobId }
    });

    this.pushChainEvent('MODEL_CALL_REQUESTED', {
      contractAddress: job.contractAddress,
      onchainJobId,
      payload: {
        modelId: job.modelId,
        mode: job.mode,
        callbackMode: job.callbackMode,
        budgetAnmNanos: job.budgetAnmNanos,
        timeoutAt: job.timeoutAt
      }
    });

    this.audit(user.id, user.role, 'contract_job_create', 'contract_job', job.id, {
      onchainJobId: job.onchainJobId,
      contractAddress: job.contractAddress,
      mode: job.mode,
      budgetAnmNanos: job.budgetAnmNanos
    });

    return {
      job: cloneDeep(job),
      escrowEvents: [funded, reserved].map((event) => cloneDeep(event)),
      contractCalls: {
        approve: {
          contractAddress: this.config.AICF_PROJECT_BALANCE_CONTRACT,
          method: 'approve',
          args: {
            spender: this.config.AICF_JOB_ESCROW_CONTRACT,
            amount_anm_nanos: budget.toString()
          }
        },
        fund: {
          contractAddress: this.config.AICF_JOB_ESCROW_CONTRACT,
          method: 'fund_job',
          args: {
            job_id: onchainJobId,
            amount_anm_nanos: budget.toString()
          }
        },
        reserve: {
          contractAddress: this.config.AICF_JOB_ESCROW_CONTRACT,
          method: 'reserve_budget',
          args: {
            job_id: onchainJobId,
            reserved_anm_nanos: budget.toString()
          }
        },
        request: {
          contractAddress: this.config.AICF_JOB_ESCROW_CONTRACT,
          method: 'create_job',
          args: {
            job_id: onchainJobId,
            contract_address: job.contractAddress,
            model_id: job.modelId,
            mode: job.mode,
            input_ref_hash: job.inputRefHash
          }
        }
      }
    };
  }

  listContractJobs(
    user: AccountUser,
    filter?: {
      contractAddress?: string;
      state?: ContractJobRecord['state'];
      modelId?: string;
      providerId?: string;
      disputeStatus?: ContractJobRecord['disputeStatus'];
    }
  ): ContractJobRecord[] {
    return [...this.store.contractJobs.values()]
      .filter((job) => {
        if (user.role !== 'admin') {
          const contract = this.resolveContractForJob(job);
          const wallet = (user.wallet?.address ?? '').toLowerCase();
          if (
            contract.ownerUserId !== user.id &&
            job.payer.toLowerCase() !== wallet &&
            job.requester.toLowerCase() !== wallet
          ) {
            return false;
          }
        }
        if (filter?.contractAddress && job.contractAddress !== filter.contractAddress.toLowerCase()) return false;
        if (filter?.state && job.state !== filter.state) return false;
        if (filter?.modelId && job.modelId !== filter.modelId) return false;
        if (filter?.providerId && !job.assignedProviderIds.includes(filter.providerId)) return false;
        if (filter?.disputeStatus && job.disputeStatus !== filter.disputeStatus) return false;
        return true;
      })
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((job) => cloneDeep(job));
  }

  getContractJob(user: AccountUser, jobId: string): ContractJobRecord {
    const job = this.resolveContractJob(jobId);
    this.assertContractJobAccess(user, job);
    return cloneDeep(job);
  }

  createAgentTask(
    user: AccountUser,
    input: {
      contractAddress: string;
      requester: string;
      payer: string;
      modelId: string;
      budgetAnmNanos: string;
      onchainTaskId?: string;
    }
  ): AgentTaskRecord {
    this.mustHaveContractJobsEnabled();
    const contract = this.resolveContractByAddress(input.contractAddress);
    this.assertContractAccess(user, contract);

    const budget = parseAnmNanos(input.budgetAnmNanos);
    if (budget <= 0n) {
      throw new Error('Invalid task budget');
    }

    const onchainTaskId = input.onchainTaskId ?? createId('onchain_task');
    if (this.store.agentTasksByOnchainId.has(onchainTaskId)) {
      throw new Error('On-chain task id already exists');
    }

    const task: AgentTaskRecord = {
      id: createId('atask'),
      onchainTaskId,
      contractAddress: contract.address,
      requester: input.requester,
      payer: input.payer,
      modelId: input.modelId,
      state: 'funded',
      budgetAnmNanos: budget.toString(),
      spentAnmNanos: '0',
      remainingAnmNanos: budget.toString(),
      stepCount: 0,
      currentStep: 0,
      finalResultHash: undefined,
      finalResultRef: undefined,
      disputeStatus: 'none',
      createdAt: nowIso(),
      updatedAt: nowIso()
    };

    this.store.agentTasks.set(task.id, task);
    this.store.agentTasksByOnchainId.set(task.onchainTaskId, task.id);
    this.pushChainEvent('AGENT_TASK_CREATED', {
      contractAddress: task.contractAddress,
      onchainTaskId: task.onchainTaskId,
      payload: {
        modelId: task.modelId,
        budgetAnmNanos: task.budgetAnmNanos
      }
    });
    this.audit(user.id, user.role, 'agent_task_create', 'agent_task', task.id, {
      onchainTaskId: task.onchainTaskId,
      contractAddress: task.contractAddress
    });
    return cloneDeep(task);
  }

  appendAgentTaskStepCommitment(
    user: AccountUser,
    taskId: string,
    input: { commitmentHash: string; traceRef?: string }
  ): AgentTaskRecord {
    const task = this.resolveAgentTask(taskId);
    const contract = this.resolveContractByAddress(task.contractAddress);
    this.assertContractAccess(user, contract);

    if (task.state !== 'running' && task.state !== 'funded') {
      throw new Error('Task is not active');
    }
    task.state = 'running';
    task.stepCount += 1;
    task.currentStep = task.stepCount;
    task.updatedAt = nowIso();

    this.pushChainEvent('AGENT_TASK_STEP_COMMITMENT', {
      contractAddress: task.contractAddress,
      onchainTaskId: task.onchainTaskId,
      payload: {
        step: task.currentStep,
        commitmentHash: input.commitmentHash,
        traceRef: input.traceRef
      }
    });
    return cloneDeep(task);
  }

  submitAgentTaskFinalResult(
    user: AccountUser,
    taskId: string,
    input: { resultHash: string; resultRef: string }
  ): AgentTaskRecord {
    const task = this.resolveAgentTask(taskId);
    const contract = this.resolveContractByAddress(task.contractAddress);
    this.assertContractAccess(user, contract);
    if (task.state !== 'running' && task.state !== 'funded') {
      throw new Error('Task is not active');
    }
    task.finalResultHash = input.resultHash;
    task.finalResultRef = input.resultRef;
    task.state = 'final_result_submitted';
    task.updatedAt = nowIso();
    return cloneDeep(task);
  }

  listAgentTasks(user: AccountUser, contractAddress?: string): AgentTaskRecord[] {
    return [...this.store.agentTasks.values()]
      .filter((task) => {
        const contract = this.resolveContractByAddress(task.contractAddress);
        if (user.role !== 'admin' && contract.ownerUserId !== user.id) return false;
        if (contractAddress && task.contractAddress !== contractAddress.toLowerCase()) return false;
        return true;
      })
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((task) => cloneDeep(task));
  }

  getAgentTask(user: AccountUser, taskId: string): AgentTaskRecord {
    const task = this.resolveAgentTask(taskId);
    const contract = this.resolveContractByAddress(task.contractAddress);
    this.assertContractAccess(user, contract);
    return cloneDeep(task);
  }

  watchChainEvents(limit = 50): ContractJobChainEvent[] {
    const take = Math.max(1, Math.min(500, Math.trunc(limit)));
    const out: ContractJobChainEvent[] = [];
    while (this.store.chainEventQueue.length > 0 && out.length < take) {
      const id = this.store.chainEventQueue.shift();
      if (!id) break;
      const evt = this.store.chainEvents.get(id);
      if (evt) out.push(cloneDeep(evt));
    }
    return out;
  }

  ingestObservedChainEvent(input: {
    eventType: ContractJobChainEvent['eventType'];
    contractAddress: string;
    onchainJobId?: string;
    onchainTaskId?: string;
    payload: Record<string, unknown>;
  }): ContractJobChainEvent {
    return this.pushChainEvent(input.eventType, {
      contractAddress: input.contractAddress.toLowerCase(),
      onchainJobId: input.onchainJobId,
      onchainTaskId: input.onchainTaskId,
      payload: input.payload
    });
  }

  scheduleContractJobs(limit = 50): {
    assigned: Array<{ jobId: string; providerId: string; nodeId: string; mode: ContractVerificationMode }>;
    skipped: string[];
  } {
    this.mustHaveContractJobsEnabled();
    const jobs = [...this.store.contractJobs.values()]
      .filter((job) => job.state === 'requested' || job.state === 'reserved')
      .slice(0, Math.max(1, Math.min(500, Math.trunc(limit))));

    const providers = [...this.store.providers.values()].filter((provider) => provider.state === 'active');
    const nodes = [...this.store.providerNodes.values()].filter((node) => node.state === 'active');

    const assigned: Array<{ jobId: string; providerId: string; nodeId: string; mode: ContractVerificationMode }> = [];
    const skipped: string[] = [];

    for (const job of jobs) {
      const allowedProviders = providers.filter((provider) => this.providerAllowedByPolicy(job, provider));
      if (allowedProviders.length === 0) {
        skipped.push(job.id);
        continue;
      }

      const targetCount = job.mode === 'QUORUM_MATCH' ? Math.max(job.replication ?? 2, 2) : 1;
      const chosen: Array<{ providerId: string; nodeId: string }> = [];
      for (const provider of allowedProviders) {
        const node = nodes.find((candidate) => candidate.providerId === provider.id);
        if (!node) continue;
        if (chosen.some((entry) => entry.providerId === provider.id)) continue;
        chosen.push({ providerId: provider.id, nodeId: node.id });
        if (chosen.length >= targetCount) break;
      }

      if (chosen.length === 0) {
        skipped.push(job.id);
        continue;
      }

      job.state = 'assigned';
      job.updatedAt = nowIso();
      job.assignedProviderIds = chosen.map((entry) => entry.providerId);

      for (const entry of chosen) {
        const queue = this.store.contractProviderQueue.get(entry.providerId) ?? [];
        queue.push(job.id);
        this.store.contractProviderQueue.set(entry.providerId, queue);

        const assignment: JobAssignmentRecord = {
          id: createId('cassign'),
          jobId: job.id,
          providerId: entry.providerId,
          nodeId: entry.nodeId,
          assignedAt: nowIso(),
          status: 'assigned'
        };
        this.store.jobAssignments.set(assignment.id, assignment);
        assigned.push({
          jobId: job.id,
          providerId: entry.providerId,
          nodeId: entry.nodeId,
          mode: job.mode
        });
      }
    }

    return { assigned, skipped };
  }

  providerClaimContractJobs(provider: ProviderProfile, nodeId: string, limit = 10): ContractJobRecord[] {
    const node = this.store.providerNodes.get(nodeId);
    if (!node || node.providerId !== provider.id) {
      throw new Error('Provider node not found');
    }

    const queue = this.store.contractProviderQueue.get(provider.id) ?? [];
    const out: ContractJobRecord[] = [];
    while (queue.length > 0 && out.length < Math.max(1, Math.min(100, Math.trunc(limit)))) {
      const jobId = queue.shift();
      if (!jobId) break;
      const job = this.store.contractJobs.get(jobId);
      if (!job) continue;
      if (!job.assignedProviderIds.includes(provider.id)) continue;
      if (job.state !== 'assigned' && job.state !== 'running') continue;
      job.state = 'running';
      job.updatedAt = nowIso();

      const assignment = [...this.store.jobAssignments.values()].find(
        (item) => item.jobId === job.id && item.providerId === provider.id && item.nodeId === nodeId
      );
      if (assignment) {
        assignment.status = 'claimed';
      }
      out.push(cloneDeep(job));
    }
    this.store.contractProviderQueue.set(provider.id, queue);
    return out;
  }

  providerSubmitContractResultCommitment(
    provider: ProviderProfile,
    jobId: string,
    input: {
      nodeId: string;
      resultHash: string;
      resultRef?: string;
      signature: string;
      modelRuntime: string;
      usage: {
        inputTokens: number;
        outputTokens: number;
        embeddingVectors: number;
        latencyMs: number;
        bytesIn: number;
        bytesOut: number;
      };
      toolTraceRef?: string;
      verifierRef?: string;
      quorumGroup?: string;
    }
  ): {
    job: ContractJobRecord;
    commitment: ResultCommitmentRecord;
  } {
    const job = this.resolveContractJob(jobId);
    if (!job.assignedProviderIds.includes(provider.id)) {
      throw new Error('Provider is not assigned for this job');
    }
    if (job.state === 'finalized_paid' || job.state === 'finalized_refunded' || job.state === 'cancelled') {
      throw new Error('Contract job is closed');
    }

    const duplicate = [...this.store.resultCommitments.values()].find(
      (item) => item.jobId === job.id && item.providerId === provider.id
    );
    if (duplicate) {
      throw new Error('Provider already submitted commitment for this job');
    }

    const commitment: ResultCommitmentRecord = {
      id: createId('ccommit'),
      jobId: job.id,
      providerId: provider.id,
      resultHash: input.resultHash,
      resultRef: input.resultRef,
      providerSignature: input.signature,
      modelRuntime: input.modelRuntime,
      usage: input.usage,
      toolTraceRef: input.toolTraceRef,
      verifierRef: input.verifierRef,
      quorumGroup: input.quorumGroup,
      createdAt: nowIso()
    };
    this.store.resultCommitments.set(commitment.id, commitment);
    job.state = input.resultRef ? 'result_submitted' : 'commitment_submitted';
    job.challengeWindowEndsAt = this.toFutureIso(this.config.AICF_CHALLENGE_WINDOW_SECONDS);
    job.updatedAt = nowIso();
    if (input.resultRef) {
      job.finalResultRef = input.resultRef;
    }

    this.pushChainEvent('RESULT_COMMITMENT_SUBMITTED', {
      contractAddress: job.contractAddress,
      onchainJobId: job.onchainJobId,
      payload: {
        providerId: provider.id,
        resultHash: input.resultHash,
        resultRef: input.resultRef
      }
    });

    if (job.mode === 'QUORUM_MATCH') {
      const sameHashCount = [...this.store.resultCommitments.values()].filter(
        (item) => item.jobId === job.id && item.resultHash === input.resultHash
      ).length;
      if (sameHashCount >= Math.max(1, job.quorum ?? 2)) {
        job.state = 'accepted';
        job.acceptedResultHash = input.resultHash;
        job.updatedAt = nowIso();
      }
    }

    if (job.mode === 'SINGLE_PROVIDER' && input.resultRef) {
      job.acceptedResultHash = input.resultHash;
    }

    const assignment = [...this.store.jobAssignments.values()].find(
      (item) => item.jobId === job.id && item.providerId === provider.id
    );
    if (assignment) {
      assignment.status = 'completed';
      assignment.completedAt = nowIso();
    }

    return {
      job: cloneDeep(job),
      commitment: cloneDeep(commitment)
    };
  }

  submitContractResultReference(
    provider: ProviderProfile,
    jobId: string,
    input: { resultRef: string }
  ): ContractJobRecord {
    const job = this.resolveContractJob(jobId);
    if (!job.assignedProviderIds.includes(provider.id)) {
      throw new Error('Provider is not assigned for this job');
    }
    const commitment = [...this.store.resultCommitments.values()].find(
      (item) => item.jobId === job.id && item.providerId === provider.id
    );
    if (!commitment) {
      throw new Error('Commitment missing');
    }
    commitment.resultRef = input.resultRef;
    job.state = 'result_submitted';
    job.finalResultRef = input.resultRef;
    job.challengeWindowEndsAt = this.toFutureIso(this.config.AICF_CHALLENGE_WINDOW_SECONDS);
    job.updatedAt = nowIso();

    this.pushChainEvent('RESULT_REFERENCE_SUBMITTED', {
      contractAddress: job.contractAddress,
      onchainJobId: job.onchainJobId,
      payload: {
        providerId: provider.id,
        resultRef: input.resultRef
      }
    });
    return cloneDeep(job);
  }

  acceptContractResult(user: AccountUser, jobId: string, acceptedHash?: string): ContractJobRecord {
    const job = this.resolveContractJob(jobId);
    this.assertContractJobAccess(user, job);

    const commitments = [...this.store.resultCommitments.values()].filter((item) => item.jobId === job.id);
    if (commitments.length === 0) {
      throw new Error('No commitments submitted');
    }

    const hash = acceptedHash ?? commitments[0].resultHash;
    if (!commitments.some((item) => item.resultHash === hash)) {
      throw new Error('Accepted hash not found among commitments');
    }

    job.acceptedResultHash = hash;
    job.state = 'accepted';
    job.updatedAt = nowIso();
    return cloneDeep(job);
  }

  openContractDispute(
    user: AccountUser,
    input: { jobId: string; reasonCode: string; evidenceRef?: string }
  ): {
    job: ContractJobRecord;
    dispute: ContractDisputeRecord;
  } {
    const job = this.resolveContractJob(input.jobId);
    this.assertContractJobAccess(user, job);
    if (job.state === 'finalized_paid' || job.state === 'finalized_refunded') {
      throw new Error('Finalized job cannot be disputed');
    }

    const existing = [...this.store.contractDisputes.values()].find((item) => item.jobId === job.id && item.status === 'open');
    if (existing) {
      throw new Error('Open dispute already exists for this job');
    }

    const dispute: ContractDisputeRecord = {
      id: createId('cdisp'),
      jobId: job.id,
      openedBy: user.id,
      reasonCode: input.reasonCode,
      evidenceRef: input.evidenceRef,
      status: 'open',
      createdAt: nowIso(),
      updatedAt: nowIso()
    };
    this.store.contractDisputes.set(dispute.id, dispute);
    job.state = 'challenged';
    job.disputeStatus = 'open';
    job.updatedAt = nowIso();

    this.pushChainEvent('DISPUTE_OPENED', {
      contractAddress: job.contractAddress,
      onchainJobId: job.onchainJobId,
      payload: {
        disputeId: dispute.id,
        reasonCode: dispute.reasonCode
      }
    });

    this.audit(user.id, user.role, 'contract_job_dispute_open', 'contract_job', job.id, {
      disputeId: dispute.id,
      reasonCode: dispute.reasonCode
    });
    return { job: cloneDeep(job), dispute: cloneDeep(dispute) };
  }

  resolveContractDispute(
    admin: AccountUser,
    input: {
      disputeId: string;
      action: 'slash' | 'clear' | 'refund_requester';
      slashAmountAnmNanos?: string;
      note?: string;
    }
  ): {
    dispute: ContractDisputeRecord;
    job: ContractJobRecord;
  } {
    if (admin.role !== 'admin') {
      throw new Error('Forbidden');
    }
    const dispute = this.store.contractDisputes.get(input.disputeId);
    if (!dispute) {
      throw new Error('Dispute not found');
    }
    if (dispute.status !== 'open') {
      throw new Error('Dispute already resolved');
    }
    const job = this.resolveContractJob(dispute.jobId);

    if (input.action === 'slash') {
      const slashAmount = parseAnmNanos(input.slashAmountAnmNanos ?? '0');
      const providerId = job.assignedProviderIds[0];
      if (providerId) {
        const provider = this.store.providers.get(providerId);
        if (provider) {
          const current = parseAnmNanos(provider.stakeAnm);
          const finalSlash = slashAmount > current ? current : slashAmount;
          provider.stakeAnm = formatAnmNanos(current - finalSlash);
          provider.slashHistory.push({
            id: createId('slash'),
            amountAnm: finalSlash.toString(),
            reason: input.note ?? 'contract_dispute_slash',
            createdAt: nowIso()
          });
          this.appendEscrowEvent({
            jobId: job.id,
            type: 'slashed',
            amountAnmNanos: finalSlash.toString(),
            actor: admin.id,
            metadata: {
              providerId
            }
          });
        }
      }
    }

    if (input.action === 'refund_requester') {
      const reserved = parseAnmNanos(job.reservedAnmNanos);
      job.refundedAnmNanos = reserved.toString();
      job.paidAnmNanos = '0';
      job.reservedAnmNanos = '0';
      job.state = 'finalized_refunded';
      this.appendEscrowEvent({
        jobId: job.id,
        type: 'refunded',
        amountAnmNanos: reserved.toString(),
        actor: admin.id
      });
    }

    if (input.action === 'clear') {
      job.state = 'result_submitted';
    }

    dispute.status = 'resolved';
    dispute.resolution = {
      action: input.action,
      slashAmountAnmNanos: input.slashAmountAnmNanos,
      note: input.note,
      resolvedBy: admin.id,
      resolvedAt: nowIso()
    };
    dispute.updatedAt = nowIso();
    job.disputeStatus = 'resolved';
    job.updatedAt = nowIso();
    return { dispute: cloneDeep(dispute), job: cloneDeep(job) };
  }

  listContractDisputes(user: AccountUser, status?: ContractDisputeRecord['status']): ContractDisputeRecord[] {
    return [...this.store.contractDisputes.values()]
      .filter((dispute) => {
        const job = this.store.contractJobs.get(dispute.jobId);
        if (!job) return false;
        if (status && dispute.status !== status) return false;
        if (user.role === 'admin') return true;
        try {
          this.assertContractJobAccess(user, job);
          return true;
        } catch {
          return false;
        }
      })
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((dispute) => cloneDeep(dispute));
  }

  listContractEscrowEvents(user: AccountUser, jobId?: string): EscrowEventRecord[] {
    return [...this.store.escrowEvents.values()]
      .filter((event) => {
        const job = this.store.contractJobs.get(event.jobId);
        if (!job) return false;
        if (jobId && job.id !== jobId) return false;
        if (user.role === 'admin') return true;
        try {
          this.assertContractJobAccess(user, job);
          return true;
        } catch {
          return false;
        }
      })
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((event) => cloneDeep(event));
  }

  listContractJobCommitments(user: AccountUser, jobId: string): ResultCommitmentRecord[] {
    const job = this.resolveContractJob(jobId);
    this.assertContractJobAccess(user, job);
    return [...this.store.resultCommitments.values()]
      .filter((item) => item.jobId === jobId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((item) => cloneDeep(item));
  }

  listContractJobAssignments(user: AccountUser, jobId: string): JobAssignmentRecord[] {
    const job = this.resolveContractJob(jobId);
    this.assertContractJobAccess(user, job);
    return [...this.store.jobAssignments.values()]
      .filter((item) => item.jobId === jobId)
      .sort((a, b) => b.assignedAt.localeCompare(a.assignedAt))
      .map((item) => cloneDeep(item));
  }

  finalizeContractJob(user: AccountUser, jobId: string): {
    job: ContractJobRecord;
    settlement: SettlementRecord;
  } {
    this.mustHaveContractJobsEnabled();
    if (!this.store.featureFlags.get('aicf.contract_jobs.finalization.enabled')?.enabled) {
      throw new Error('Contract finalization worker is disabled');
    }

    const job = this.resolveContractJob(jobId);
    this.assertContractJobAccess(user, job);
    if (job.disputeStatus === 'open') {
      throw new Error('Cannot finalize while dispute is open');
    }
    if (job.state === 'finalized_paid' || job.state === 'finalized_refunded') {
      throw new Error('Already finalized');
    }

    const commitments = [...this.store.resultCommitments.values()].filter((item) => item.jobId === job.id);
    if (commitments.length === 0) {
      throw new Error('No commitments for finalization');
    }

    if (job.mode === 'CALLBACK_ACCEPT' && job.state !== 'accepted') {
      throw new Error('Callback-accept job requires explicit acceptance before finalization');
    }

    if (job.mode === 'QUORUM_MATCH' && job.state !== 'accepted') {
      throw new Error('Quorum job requires accepted quorum hash before finalization');
    }

    if (job.mode === 'VERIFIER_REVIEW' && job.state !== 'accepted') {
      throw new Error('Verifier-review job requires acceptance before finalization');
    }

    if (job.mode === 'SINGLE_PROVIDER') {
      if (!job.challengeWindowEndsAt) {
        throw new Error('Challenge window not started');
      }
      if (!this.isPast(job.challengeWindowEndsAt)) {
        throw new Error('Challenge window not yet elapsed');
      }
    }

    const accepted = job.acceptedResultHash
      ? commitments.find((item) => item.resultHash === job.acceptedResultHash)
      : commitments[0];
    if (!accepted) {
      throw new Error('Accepted commitment not found');
    }

    const model = this.getModel(job.modelId);
    const charge = calculateCharge({
      pricing: model.pricing,
      inputTokens: accepted.usage.inputTokens,
      outputTokens: accepted.usage.outputTokens,
      embeddingVectors: accepted.usage.embeddingVectors,
      subsidyBps: this.config.AICF_DEFAULT_SUBSIDY_BPS
    });

    const reserved = parseAnmNanos(job.reservedAnmNanos);
    const payout = charge.providerRewardAnmNanos > reserved ? reserved : charge.providerRewardAnmNanos;
    const charged = charge.netChargeAnmNanos > reserved ? reserved : charge.netChargeAnmNanos;
    const refund = reserved - charged;

    job.paidAnmNanos = payout.toString();
    job.refundedAnmNanos = refund.toString();
    job.reservedAnmNanos = '0';
    job.state = 'finalized_paid';
    job.updatedAt = nowIso();

    this.appendEscrowEvent({
      jobId: job.id,
      type: 'paid',
      amountAnmNanos: payout.toString(),
      actor: user.id,
      metadata: {
        providerId: accepted.providerId
      }
    });
    if (refund > 0n) {
      this.appendEscrowEvent({
        jobId: job.id,
        type: 'refunded',
        amountAnmNanos: refund.toString(),
        actor: user.id
      });
    }

    const providerRewards = this.store.rewardLedger.get(accepted.providerId) ?? 0n;
    this.store.rewardLedger.set(accepted.providerId, providerRewards + payout);

    const settlement: SettlementRecord = {
      id: createId('settlement'),
      jobId: job.id,
      providerId: accepted.providerId,
      projectId: 'contract_jobs',
      chargeAnmNanos: charged.toString(),
      providerRewardAnmNanos: payout.toString(),
      treasuryCutAnmNanos: (charged - payout).toString(),
      subsidyAnmNanos: charge.subsidyAnmNanos.toString(),
      status: 'queued_onchain',
      createdAt: nowIso()
    };
    this.store.settlements.set(settlement.id, settlement);
    this.pushChainEvent('FINALIZED', {
      contractAddress: job.contractAddress,
      onchainJobId: job.onchainJobId,
      payload: {
        settlementId: settlement.id,
        payoutAnmNanos: payout.toString(),
        refundAnmNanos: refund.toString()
      }
    });

    return { job: cloneDeep(job), settlement: cloneDeep(settlement) };
  }

  refundContractJobIfExpired(user: AccountUser, jobId: string): ContractJobRecord {
    const job = this.resolveContractJob(jobId);
    this.assertContractJobAccess(user, job);
    if (job.state === 'finalized_paid' || job.state === 'finalized_refunded' || job.state === 'cancelled') {
      throw new Error('Closed jobs cannot be refunded');
    }
    if (!this.isPast(job.timeoutAt)) {
      throw new Error('Job has not expired');
    }
    const reserved = parseAnmNanos(job.reservedAnmNanos);
    job.reservedAnmNanos = '0';
    job.refundedAnmNanos = (parseAnmNanos(job.refundedAnmNanos) + reserved).toString();
    job.state = 'expired';
    job.updatedAt = nowIso();

    this.appendEscrowEvent({
      jobId: job.id,
      type: 'refunded',
      amountAnmNanos: reserved.toString(),
      actor: user.id,
      metadata: { reason: 'expired' }
    });
    return cloneDeep(job);
  }

  contractFinalizationTick(limit = 20): {
    finalized: string[];
    skipped: string[];
  } {
    const eligible = [...this.store.contractJobs.values()]
      .filter((job) => job.mode === 'SINGLE_PROVIDER' && (job.state === 'result_submitted' || job.state === 'accepted'))
      .slice(0, Math.max(1, Math.min(500, Math.trunc(limit))));
    const finalized: string[] = [];
    const skipped: string[] = [];
    const admin = [...this.store.users.values()].find((user) => user.role === 'admin');
    if (!admin) {
      return { finalized, skipped: eligible.map((job) => job.id) };
    }
    for (const job of eligible) {
      try {
        this.finalizeContractJob(admin, job.id);
        finalized.push(job.id);
      } catch {
        skipped.push(job.id);
      }
    }
    return { finalized, skipped };
  }

  contractResultSubmitterTick(limit = 30): {
    moved: string[];
  } {
    const jobs = [...this.store.contractJobs.values()]
      .filter((job) => job.state === 'commitment_submitted')
      .slice(0, Math.max(1, Math.min(500, Math.trunc(limit))));
    const moved: string[] = [];
    for (const job of jobs) {
      const hasRef = [...this.store.resultCommitments.values()].some((item) => item.jobId === job.id && !!item.resultRef);
      if (hasRef) {
        job.state = 'result_submitted';
        job.updatedAt = nowIso();
        moved.push(job.id);
      }
    }
    return { moved };
  }

  health(): {
    ok: boolean;
    counts: {
      users: number;
      projects: number;
      providers: number;
      nodes: number;
      jobs: number;
      contractJobs: number;
      agentTasks: number;
      contractDisputes: number;
      usage: number;
      settlements: number;
    };
  } {
    return {
      ok: true,
      counts: {
        users: this.store.users.size,
        projects: this.store.projects.size,
        providers: this.store.providers.size,
        nodes: this.store.providerNodes.size,
        jobs: this.store.jobs.size,
        contractJobs: this.store.contractJobs.size,
        agentTasks: this.store.agentTasks.size,
        contractDisputes: this.store.contractDisputes.size,
        usage: this.store.usage.size,
        settlements: this.store.settlements.size
      }
    };
  }

  private audit(
    actorId: string,
    actorRole: AuditLogEntry['actorRole'],
    action: string,
    resourceType: string,
    resourceId: string,
    metadata?: Record<string, unknown>
  ): void {
    const event: AuditLogEntry = {
      id: createId('audit'),
      actorId,
      actorRole,
      action,
      resourceType,
      resourceId,
      createdAt: nowIso(),
      metadata
    };
    this.store.auditLogs.set(event.id, event);
    this.logger.info(
      {
        action,
        resourceType,
        resourceId,
        actorId,
        actorRole,
        metadata
      },
      'audit'
    );
  }
}
