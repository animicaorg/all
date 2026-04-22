export type PurchaseStatus =
  | 'CREATED'
  | 'FUNDS_PENDING'
  | 'FUNDS_SETTLED'
  | 'MINT_AUTHORIZED'
  | 'MINT_SUBMITTED'
  | 'MINT_CONFIRMED'
  | 'FAILED'
  | 'CANCELLED';

export type RedemptionStatus =
  | 'REQUESTED'
  | 'PENDING_SIGNATURE'
  | 'ONCHAIN_PENDING'
  | 'ONCHAIN_CONFIRMED'
  | 'PAYOUT_PENDING'
  | 'PAYOUT_SENT'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'FAILED';

export type KycStatus = 'NOT_STARTED' | 'PENDING' | 'APPROVED' | 'REJECTED' | 'REVIEW';

export interface UserRecord {
  id: string;
  email?: string;
  role: 'USER' | 'ADMIN' | 'COMPLIANCE' | 'OPS' | 'SUPPORT';
  createdAt: string;
}

export interface WalletLinkRecord {
  id: string;
  userId: string;
  walletAddress: string;
  chainId: number;
  isPrimary: boolean;
  linkedAt: string;
}

export interface KycRecord {
  id: string;
  userId: string;
  status: KycStatus;
  provider: string;
  createdAt: string;
  updatedAt: string;
}

export interface BankAccountRecord {
  id: string;
  userId: string;
  bankAccountHash: string;
  status: 'PENDING_VERIFICATION' | 'VERIFIED' | 'REJECTED' | 'DISABLED';
  createdAt: string;
}

export interface PurchaseIntentRecord {
  id: string;
  userId: string;
  walletAddress: string;
  bankAccountId?: string;
  amountUsd: string;
  amountUsdan: string;
  status: PurchaseStatus;
  modernTreasuryRef?: string;
  settlementReference?: string;
  requestId: string;
  nonce: string;
  mintTxHash?: string;
  createdAt: string;
  updatedAt: string;
}

export interface RedemptionRequestRecord {
  id: string;
  userId: string;
  walletAddress: string;
  bankAccountId?: string;
  amountUsdan: string;
  status: RedemptionStatus;
  requestNonce: string;
  userIntentHash?: string;
  onchainTxHash?: string;
  payoutReference?: string;
  cancellationReason?: string;
  createdAt: string;
  updatedAt: string;
}

export interface MintAuthorizationRecord {
  id: string;
  purchaseIntentId: string;
  userId: string;
  walletAddress: string;
  amountUsdan: string;
  requestId: string;
  nonce: string;
  digestHex: string;
  signatureHex?: string;
  status: 'PREPARED' | 'SIGNED' | 'SUBMITTED' | 'CONFIRMED' | 'VOIDED';
  txHash?: string;
  createdAt: string;
}

export interface FiatPaymentEventRecord {
  id: string;
  purchaseIntentId?: string;
  redemptionRequestId?: string;
  provider: string;
  eventType: string;
  externalId?: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface ReserveSnapshotRecord {
  id: string;
  capturedAt: string;
  source: 'RECONCILIATION' | 'MANUAL' | 'ATTESTATION';
  tokenSupply: string;
  reserveLedgerBalance: string;
  outstandingRedemptionQueue: string;
  pendingMintQueue: string;
  coverageRatioBps: number;
  latestAttestationAt?: string;
  attestationHash?: string;
  attestationUri?: string;
  reconciliationHash: string;
  signedStatementHash?: string;
}

export interface WebhookDeliveryRecord {
  id: string;
  provider: string;
  eventId: string;
  idempotencyKey?: string;
  status: 'RECEIVED' | 'PROCESSED' | 'FAILED';
  signatureValid: boolean;
  payload: Record<string, unknown>;
  attemptCount: number;
  lastError?: string;
  createdAt: string;
}

export interface ComplianceFlagRecord {
  id: string;
  userId: string;
  type: 'SANCTIONS' | 'AML' | 'MANUAL_REVIEW' | 'VELOCITY' | 'LIMIT';
  status: 'OPEN' | 'RESOLVED';
  reason: string;
  createdBy: string;
  createdAt: string;
}

export interface SupportTicketRecord {
  id: string;
  userId: string;
  subject: string;
  message: string;
  status: 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CLOSED';
  priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'URGENT';
  createdAt: string;
  updatedAt: string;
}

export interface AdminActionRecord {
  id: string;
  actorId: string;
  action: string;
  targetType: string;
  targetId: string;
  reason?: string;
  createdAt: string;
}

export interface AuditLogRecord {
  id: string;
  actorType: string;
  actorId: string;
  action: string;
  entityType: string;
  entityId: string;
  requestId?: string;
  idempotencyKey?: string;
  details?: Record<string, unknown>;
  createdAt: string;
}

export interface UsdanStore {
  createUser(input: Omit<UserRecord, 'createdAt'>): Promise<UserRecord>;
  getUserById(id: string): Promise<UserRecord | null>;

  upsertWalletLink(input: Omit<WalletLinkRecord, 'id' | 'linkedAt'>): Promise<WalletLinkRecord>;
  getWalletLinks(userId: string): Promise<WalletLinkRecord[]>;

  upsertKycRecord(input: Omit<KycRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<KycRecord>;
  getLatestKyc(userId: string): Promise<KycRecord | null>;

  upsertBankAccount(input: Omit<BankAccountRecord, 'id' | 'createdAt'>): Promise<BankAccountRecord>;
  getBankAccount(id: string): Promise<BankAccountRecord | null>;
  listBankAccounts(userId: string): Promise<BankAccountRecord[]>;

  createPurchaseIntent(input: Omit<PurchaseIntentRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<PurchaseIntentRecord>;
  updatePurchaseIntent(id: string, patch: Partial<PurchaseIntentRecord>): Promise<PurchaseIntentRecord>;
  getPurchaseIntent(id: string): Promise<PurchaseIntentRecord | null>;
  listPurchaseIntents(userId?: string): Promise<PurchaseIntentRecord[]>;

  createMintAuthorization(input: Omit<MintAuthorizationRecord, 'id' | 'createdAt'>): Promise<MintAuthorizationRecord>;
  updateMintAuthorization(id: string, patch: Partial<MintAuthorizationRecord>): Promise<MintAuthorizationRecord>;
  getMintAuthorizationByPurchase(purchaseIntentId: string): Promise<MintAuthorizationRecord | null>;

  createRedemptionRequest(input: Omit<RedemptionRequestRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<RedemptionRequestRecord>;
  updateRedemptionRequest(id: string, patch: Partial<RedemptionRequestRecord>): Promise<RedemptionRequestRecord>;
  getRedemptionRequest(id: string): Promise<RedemptionRequestRecord | null>;
  listRedemptionRequests(userId?: string): Promise<RedemptionRequestRecord[]>;

  createFiatPaymentEvent(input: Omit<FiatPaymentEventRecord, 'id' | 'createdAt'>): Promise<FiatPaymentEventRecord>;

  createReserveSnapshot(input: Omit<ReserveSnapshotRecord, 'id' | 'capturedAt'>): Promise<ReserveSnapshotRecord>;
  listReserveSnapshots(limit?: number): Promise<ReserveSnapshotRecord[]>;

  createWebhookDelivery(input: Omit<WebhookDeliveryRecord, 'id' | 'createdAt'>): Promise<WebhookDeliveryRecord>;
  updateWebhookDelivery(id: string, patch: Partial<WebhookDeliveryRecord>): Promise<WebhookDeliveryRecord>;
  findWebhookDelivery(provider: string, eventId: string): Promise<WebhookDeliveryRecord | null>;
  listWebhookDeliveries(limit?: number): Promise<WebhookDeliveryRecord[]>;

  createComplianceFlag(input: Omit<ComplianceFlagRecord, 'id' | 'createdAt'>): Promise<ComplianceFlagRecord>;
  listOpenComplianceFlags(userId: string): Promise<ComplianceFlagRecord[]>;

  createSupportTicket(input: Omit<SupportTicketRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<SupportTicketRecord>;
  listSupportTickets(userId?: string): Promise<SupportTicketRecord[]>;

  createAdminAction(input: Omit<AdminActionRecord, 'id' | 'createdAt'>): Promise<AdminActionRecord>;
  createAuditLog(input: Omit<AuditLogRecord, 'id' | 'createdAt'>): Promise<AuditLogRecord>;
}
