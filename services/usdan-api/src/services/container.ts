import type { Config } from '../config.js';
import type { Logger } from '../logger.js';
import type { TreasuryProvider } from '../providers/treasury/provider.js';
import type { UsdanStore } from '../store/types.js';
import { AdminService } from './adminService.js';
import { AuditService } from './auditService.js';
import { KycService } from './kycService.js';
import { MintAuthorizationService } from './mintAuthorizationService.js';
import { PurchaseService } from './purchaseService.js';
import { RedemptionService } from './redemptionService.js';
import { ReserveService, StubChainReadClient } from './reserveService.js';
import { RiskEngine } from './riskEngine.js';
import { SupportService } from './supportService.js';
import { TransactionHistoryService } from './transactionHistoryService.js';
import { WalletBindingService } from './walletBindingService.js';
import { WebhookService } from './webhookService.js';

export interface ServiceContainer {
  walletBinding: WalletBindingService;
  kyc: KycService;
  risk: RiskEngine;
  mintAuth: MintAuthorizationService;
  purchase: PurchaseService;
  redemption: RedemptionService;
  reserve: ReserveService;
  transactions: TransactionHistoryService;
  support: SupportService;
  admin: AdminService;
  audit: AuditService;
  webhook: WebhookService;
  chain: StubChainReadClient;
}

export function createServiceContainer(input: {
  config: Config;
  logger: Logger;
  store: UsdanStore;
  treasury: TreasuryProvider;
}): ServiceContainer {
  const audit = new AuditService(input.store);
  const kyc = new KycService(input.store);
  const risk = new RiskEngine(input.store);
  const mintAuth = new MintAuthorizationService(input.store, input.config);
  const chain = new StubChainReadClient('0.00');
  const reserve = new ReserveService(input.store, input.treasury, chain, input.config);

  const purchase = new PurchaseService(
    input.store,
    input.treasury,
    kyc,
    risk,
    mintAuth,
    audit
  );
  const redemption = new RedemptionService(
    input.store,
    input.treasury,
    kyc,
    risk,
    audit
  );
  const transactions = new TransactionHistoryService(input.store);
  const support = new SupportService(input.store);
  const admin = new AdminService(input.store, reserve);
  const walletBinding = new WalletBindingService(input.store, input.config);
  const webhook = new WebhookService(input.store, input.treasury, purchase, redemption);

  input.logger.info('USDAN service container initialized');

  return {
    walletBinding,
    kyc,
    risk,
    mintAuth,
    purchase,
    redemption,
    reserve,
    transactions,
    support,
    admin,
    audit,
    webhook,
    chain
  };
}
