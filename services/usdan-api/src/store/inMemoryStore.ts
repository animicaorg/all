import { randomUUID } from 'node:crypto';
import type {
  AdminActionRecord,
  AuditLogRecord,
  BankAccountRecord,
  ComplianceFlagRecord,
  FiatPaymentEventRecord,
  KycRecord,
  MintAuthorizationRecord,
  PurchaseIntentRecord,
  RedemptionRequestRecord,
  ReserveSnapshotRecord,
  SupportTicketRecord,
  UsdanStore,
  UserRecord,
  WalletLinkRecord,
  WebhookDeliveryRecord
} from './types.js';

function nowIso() {
  return new Date().toISOString();
}

function patchEntity<T extends { id: string }>(entity: T, patch: Partial<T>): T {
  return { ...entity, ...patch };
}

export class InMemoryUsdanStore implements UsdanStore {
  private users = new Map<string, UserRecord>();
  private walletLinks = new Map<string, WalletLinkRecord>();
  private kycRecords = new Map<string, KycRecord>();
  private bankAccounts = new Map<string, BankAccountRecord>();
  private purchases = new Map<string, PurchaseIntentRecord>();
  private mintAuths = new Map<string, MintAuthorizationRecord>();
  private redemptions = new Map<string, RedemptionRequestRecord>();
  private fiatEvents = new Map<string, FiatPaymentEventRecord>();
  private snapshots = new Map<string, ReserveSnapshotRecord>();
  private webhooks = new Map<string, WebhookDeliveryRecord>();
  private complianceFlags = new Map<string, ComplianceFlagRecord>();
  private supportTickets = new Map<string, SupportTicketRecord>();
  private adminActions = new Map<string, AdminActionRecord>();
  private auditLogs = new Map<string, AuditLogRecord>();

  async createUser(input: Omit<UserRecord, 'createdAt'>): Promise<UserRecord> {
    const rec: UserRecord = { ...input, createdAt: nowIso() };
    this.users.set(rec.id, rec);
    return rec;
  }

  async getUserById(id: string): Promise<UserRecord | null> {
    return this.users.get(id) ?? null;
  }

  async upsertWalletLink(input: Omit<WalletLinkRecord, 'id' | 'linkedAt'>): Promise<WalletLinkRecord> {
    const existing = [...this.walletLinks.values()].find(
      (x) => x.walletAddress === input.walletAddress && x.chainId === input.chainId
    );
    if (existing) {
      const next = patchEntity(existing, { ...input });
      this.walletLinks.set(existing.id, next);
      return next;
    }
    const rec: WalletLinkRecord = {
      id: randomUUID(),
      linkedAt: nowIso(),
      ...input
    };
    this.walletLinks.set(rec.id, rec);
    return rec;
  }

  async getWalletLinks(userId: string): Promise<WalletLinkRecord[]> {
    return [...this.walletLinks.values()].filter((x) => x.userId === userId);
  }

  async upsertKycRecord(input: Omit<KycRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<KycRecord> {
    const existing = [...this.kycRecords.values()].find((x) => x.userId === input.userId);
    if (existing) {
      const next = patchEntity(existing, { ...input, updatedAt: nowIso() });
      this.kycRecords.set(existing.id, next);
      return next;
    }
    const t = nowIso();
    const rec: KycRecord = {
      id: randomUUID(),
      createdAt: t,
      updatedAt: t,
      ...input
    };
    this.kycRecords.set(rec.id, rec);
    return rec;
  }

  async getLatestKyc(userId: string): Promise<KycRecord | null> {
    const items = [...this.kycRecords.values()]
      .filter((x) => x.userId === userId)
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return items[0] ?? null;
  }

  async upsertBankAccount(input: Omit<BankAccountRecord, 'id' | 'createdAt'>): Promise<BankAccountRecord> {
    const existing = [...this.bankAccounts.values()].find((x) => x.userId === input.userId && x.bankAccountHash === input.bankAccountHash);
    if (existing) {
      const next = patchEntity(existing, input);
      this.bankAccounts.set(existing.id, next);
      return next;
    }
    const rec: BankAccountRecord = {
      id: randomUUID(),
      createdAt: nowIso(),
      ...input
    };
    this.bankAccounts.set(rec.id, rec);
    return rec;
  }

  async getBankAccount(id: string): Promise<BankAccountRecord | null> {
    return this.bankAccounts.get(id) ?? null;
  }

  async listBankAccounts(userId: string): Promise<BankAccountRecord[]> {
    return [...this.bankAccounts.values()].filter((x) => x.userId === userId);
  }

  async createPurchaseIntent(input: Omit<PurchaseIntentRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<PurchaseIntentRecord> {
    const t = nowIso();
    const rec: PurchaseIntentRecord = {
      id: randomUUID(),
      createdAt: t,
      updatedAt: t,
      ...input
    };
    this.purchases.set(rec.id, rec);
    return rec;
  }

  async updatePurchaseIntent(id: string, patch: Partial<PurchaseIntentRecord>): Promise<PurchaseIntentRecord> {
    const existing = this.purchases.get(id);
    if (!existing) throw new Error(`purchase_intent_not_found:${id}`);
    const next = patchEntity(existing, { ...patch, updatedAt: nowIso() });
    this.purchases.set(id, next);
    return next;
  }

  async getPurchaseIntent(id: string): Promise<PurchaseIntentRecord | null> {
    return this.purchases.get(id) ?? null;
  }

  async listPurchaseIntents(userId?: string): Promise<PurchaseIntentRecord[]> {
    const all = [...this.purchases.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return userId ? all.filter((x) => x.userId === userId) : all;
  }

  async createMintAuthorization(input: Omit<MintAuthorizationRecord, 'id' | 'createdAt'>): Promise<MintAuthorizationRecord> {
    const rec: MintAuthorizationRecord = {
      id: randomUUID(),
      createdAt: nowIso(),
      ...input
    };
    this.mintAuths.set(rec.id, rec);
    return rec;
  }

  async updateMintAuthorization(id: string, patch: Partial<MintAuthorizationRecord>): Promise<MintAuthorizationRecord> {
    const existing = this.mintAuths.get(id);
    if (!existing) throw new Error(`mint_auth_not_found:${id}`);
    const next = patchEntity(existing, patch);
    this.mintAuths.set(id, next);
    return next;
  }

  async getMintAuthorizationByPurchase(purchaseIntentId: string): Promise<MintAuthorizationRecord | null> {
    return [...this.mintAuths.values()].find((x) => x.purchaseIntentId === purchaseIntentId) ?? null;
  }

  async createRedemptionRequest(input: Omit<RedemptionRequestRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<RedemptionRequestRecord> {
    const t = nowIso();
    const rec: RedemptionRequestRecord = {
      id: randomUUID(),
      createdAt: t,
      updatedAt: t,
      ...input
    };
    this.redemptions.set(rec.id, rec);
    return rec;
  }

  async updateRedemptionRequest(id: string, patch: Partial<RedemptionRequestRecord>): Promise<RedemptionRequestRecord> {
    const existing = this.redemptions.get(id);
    if (!existing) throw new Error(`redemption_not_found:${id}`);
    const next = patchEntity(existing, { ...patch, updatedAt: nowIso() });
    this.redemptions.set(id, next);
    return next;
  }

  async getRedemptionRequest(id: string): Promise<RedemptionRequestRecord | null> {
    return this.redemptions.get(id) ?? null;
  }

  async listRedemptionRequests(userId?: string): Promise<RedemptionRequestRecord[]> {
    const all = [...this.redemptions.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return userId ? all.filter((x) => x.userId === userId) : all;
  }

  async createFiatPaymentEvent(input: Omit<FiatPaymentEventRecord, 'id' | 'createdAt'>): Promise<FiatPaymentEventRecord> {
    const rec: FiatPaymentEventRecord = {
      id: randomUUID(),
      createdAt: nowIso(),
      ...input
    };
    this.fiatEvents.set(rec.id, rec);
    return rec;
  }

  async createReserveSnapshot(input: Omit<ReserveSnapshotRecord, 'id' | 'capturedAt'>): Promise<ReserveSnapshotRecord> {
    const rec: ReserveSnapshotRecord = {
      id: randomUUID(),
      capturedAt: nowIso(),
      ...input
    };
    this.snapshots.set(rec.id, rec);
    return rec;
  }

  async listReserveSnapshots(limit = 30): Promise<ReserveSnapshotRecord[]> {
    return [...this.snapshots.values()].sort((a, b) => b.capturedAt.localeCompare(a.capturedAt)).slice(0, limit);
  }

  async createWebhookDelivery(input: Omit<WebhookDeliveryRecord, 'id' | 'createdAt'>): Promise<WebhookDeliveryRecord> {
    const rec: WebhookDeliveryRecord = {
      id: randomUUID(),
      createdAt: nowIso(),
      ...input
    };
    this.webhooks.set(rec.id, rec);
    return rec;
  }

  async updateWebhookDelivery(id: string, patch: Partial<WebhookDeliveryRecord>): Promise<WebhookDeliveryRecord> {
    const existing = this.webhooks.get(id);
    if (!existing) throw new Error(`webhook_not_found:${id}`);
    const next = patchEntity(existing, patch);
    this.webhooks.set(id, next);
    return next;
  }

  async findWebhookDelivery(provider: string, eventId: string): Promise<WebhookDeliveryRecord | null> {
    return [...this.webhooks.values()].find((x) => x.provider === provider && x.eventId === eventId) ?? null;
  }

  async listWebhookDeliveries(limit = 100): Promise<WebhookDeliveryRecord[]> {
    return [...this.webhooks.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, limit);
  }

  async createComplianceFlag(input: Omit<ComplianceFlagRecord, 'id' | 'createdAt'>): Promise<ComplianceFlagRecord> {
    const rec: ComplianceFlagRecord = {
      id: randomUUID(),
      createdAt: nowIso(),
      ...input
    };
    this.complianceFlags.set(rec.id, rec);
    return rec;
  }

  async listOpenComplianceFlags(userId: string): Promise<ComplianceFlagRecord[]> {
    return [...this.complianceFlags.values()].filter((x) => x.userId === userId && x.status === 'OPEN');
  }

  async createSupportTicket(input: Omit<SupportTicketRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<SupportTicketRecord> {
    const t = nowIso();
    const rec: SupportTicketRecord = {
      id: randomUUID(),
      createdAt: t,
      updatedAt: t,
      ...input
    };
    this.supportTickets.set(rec.id, rec);
    return rec;
  }

  async listSupportTickets(userId?: string): Promise<SupportTicketRecord[]> {
    const all = [...this.supportTickets.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return userId ? all.filter((x) => x.userId === userId) : all;
  }

  async createAdminAction(input: Omit<AdminActionRecord, 'id' | 'createdAt'>): Promise<AdminActionRecord> {
    const rec: AdminActionRecord = {
      id: randomUUID(),
      createdAt: nowIso(),
      ...input
    };
    this.adminActions.set(rec.id, rec);
    return rec;
  }

  async createAuditLog(input: Omit<AuditLogRecord, 'id' | 'createdAt'>): Promise<AuditLogRecord> {
    const rec: AuditLogRecord = {
      id: randomUUID(),
      createdAt: nowIso(),
      ...input
    };
    this.auditLogs.set(rec.id, rec);
    return rec;
  }
}
