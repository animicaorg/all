import type { PrismaClient } from '@prisma/client';
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
import { InMemoryUsdanStore } from './inMemoryStore.js';

/**
 * Prisma-backed store shim.
 *
 * This class is intentionally thin: core data model is captured in prisma/schema.prisma,
 * and this adapter can be incrementally replaced method-by-method with direct Prisma queries.
 */
export class PrismaUsdanStore implements UsdanStore {
  private readonly memoryFallback = new InMemoryUsdanStore();

  constructor(private readonly prisma: PrismaClient) {}

  // NOTE: The initial production skeleton keeps behavior aligned with InMemoryUsdanStore
  // while teams map each method to exact Prisma queries and transactional boundaries.
  async createUser(input: Omit<UserRecord, 'createdAt'>): Promise<UserRecord> {
    await this.prisma.$executeRawUnsafe('SELECT 1');
    return this.memoryFallback.createUser(input);
  }

  async getUserById(id: string): Promise<UserRecord | null> {
    await this.prisma.$executeRawUnsafe('SELECT 1');
    return this.memoryFallback.getUserById(id);
  }

  async upsertWalletLink(input: Omit<WalletLinkRecord, 'id' | 'linkedAt'>): Promise<WalletLinkRecord> {
    await this.prisma.$executeRawUnsafe('SELECT 1');
    return this.memoryFallback.upsertWalletLink(input);
  }

  async getWalletLinks(userId: string): Promise<WalletLinkRecord[]> {
    return this.memoryFallback.getWalletLinks(userId);
  }

  async upsertKycRecord(input: Omit<KycRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<KycRecord> {
    return this.memoryFallback.upsertKycRecord(input);
  }

  async getLatestKyc(userId: string): Promise<KycRecord | null> {
    return this.memoryFallback.getLatestKyc(userId);
  }

  async upsertBankAccount(input: Omit<BankAccountRecord, 'id' | 'createdAt'>): Promise<BankAccountRecord> {
    return this.memoryFallback.upsertBankAccount(input);
  }

  async getBankAccount(id: string): Promise<BankAccountRecord | null> {
    return this.memoryFallback.getBankAccount(id);
  }

  async listBankAccounts(userId: string): Promise<BankAccountRecord[]> {
    return this.memoryFallback.listBankAccounts(userId);
  }

  async createPurchaseIntent(input: Omit<PurchaseIntentRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<PurchaseIntentRecord> {
    return this.memoryFallback.createPurchaseIntent(input);
  }

  async updatePurchaseIntent(id: string, patch: Partial<PurchaseIntentRecord>): Promise<PurchaseIntentRecord> {
    return this.memoryFallback.updatePurchaseIntent(id, patch);
  }

  async getPurchaseIntent(id: string): Promise<PurchaseIntentRecord | null> {
    return this.memoryFallback.getPurchaseIntent(id);
  }

  async listPurchaseIntents(userId?: string): Promise<PurchaseIntentRecord[]> {
    return this.memoryFallback.listPurchaseIntents(userId);
  }

  async createMintAuthorization(input: Omit<MintAuthorizationRecord, 'id' | 'createdAt'>): Promise<MintAuthorizationRecord> {
    return this.memoryFallback.createMintAuthorization(input);
  }

  async updateMintAuthorization(id: string, patch: Partial<MintAuthorizationRecord>): Promise<MintAuthorizationRecord> {
    return this.memoryFallback.updateMintAuthorization(id, patch);
  }

  async getMintAuthorizationByPurchase(purchaseIntentId: string): Promise<MintAuthorizationRecord | null> {
    return this.memoryFallback.getMintAuthorizationByPurchase(purchaseIntentId);
  }

  async createRedemptionRequest(input: Omit<RedemptionRequestRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<RedemptionRequestRecord> {
    return this.memoryFallback.createRedemptionRequest(input);
  }

  async updateRedemptionRequest(id: string, patch: Partial<RedemptionRequestRecord>): Promise<RedemptionRequestRecord> {
    return this.memoryFallback.updateRedemptionRequest(id, patch);
  }

  async getRedemptionRequest(id: string): Promise<RedemptionRequestRecord | null> {
    return this.memoryFallback.getRedemptionRequest(id);
  }

  async listRedemptionRequests(userId?: string): Promise<RedemptionRequestRecord[]> {
    return this.memoryFallback.listRedemptionRequests(userId);
  }

  async createFiatPaymentEvent(input: Omit<FiatPaymentEventRecord, 'id' | 'createdAt'>): Promise<FiatPaymentEventRecord> {
    return this.memoryFallback.createFiatPaymentEvent(input);
  }

  async createReserveSnapshot(input: Omit<ReserveSnapshotRecord, 'id' | 'capturedAt'>): Promise<ReserveSnapshotRecord> {
    return this.memoryFallback.createReserveSnapshot(input);
  }

  async listReserveSnapshots(limit?: number): Promise<ReserveSnapshotRecord[]> {
    return this.memoryFallback.listReserveSnapshots(limit);
  }

  async createWebhookDelivery(input: Omit<WebhookDeliveryRecord, 'id' | 'createdAt'>): Promise<WebhookDeliveryRecord> {
    return this.memoryFallback.createWebhookDelivery(input);
  }

  async updateWebhookDelivery(id: string, patch: Partial<WebhookDeliveryRecord>): Promise<WebhookDeliveryRecord> {
    return this.memoryFallback.updateWebhookDelivery(id, patch);
  }

  async findWebhookDelivery(provider: string, eventId: string): Promise<WebhookDeliveryRecord | null> {
    return this.memoryFallback.findWebhookDelivery(provider, eventId);
  }

  async listWebhookDeliveries(limit?: number): Promise<WebhookDeliveryRecord[]> {
    return this.memoryFallback.listWebhookDeliveries(limit);
  }

  async createComplianceFlag(input: Omit<ComplianceFlagRecord, 'id' | 'createdAt'>): Promise<ComplianceFlagRecord> {
    return this.memoryFallback.createComplianceFlag(input);
  }

  async listOpenComplianceFlags(userId: string): Promise<ComplianceFlagRecord[]> {
    return this.memoryFallback.listOpenComplianceFlags(userId);
  }

  async createSupportTicket(input: Omit<SupportTicketRecord, 'id' | 'createdAt' | 'updatedAt'>): Promise<SupportTicketRecord> {
    return this.memoryFallback.createSupportTicket(input);
  }

  async listSupportTickets(userId?: string): Promise<SupportTicketRecord[]> {
    return this.memoryFallback.listSupportTickets(userId);
  }

  async createAdminAction(input: Omit<AdminActionRecord, 'id' | 'createdAt'>): Promise<AdminActionRecord> {
    return this.memoryFallback.createAdminAction(input);
  }

  async createAuditLog(input: Omit<AuditLogRecord, 'id' | 'createdAt'>): Promise<AuditLogRecord> {
    return this.memoryFallback.createAuditLog(input);
  }
}
