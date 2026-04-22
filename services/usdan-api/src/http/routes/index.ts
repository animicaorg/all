import type { Router } from 'express';
import { createAdminRouter } from './admin.js';
import { createAuthRouter } from './auth.js';
import type { HttpContext } from '../context.js';
import { createBuyRouter } from './buy.js';
import { createHealthRouter } from './health.js';
import { createKycRouter } from './kyc.js';
import { createRedeemRouter } from './redeem.js';
import { createReservesRouter } from './reserves.js';
import { createSupportRouter } from './support.js';
import { createTransactionsRouter } from './transactions.js';
import { createWalletRouter } from './wallet.js';
import { createWebhookRouter } from './webhooks.js';

export interface AppRouters {
  health: Router;
  auth: Router;
  wallet: Router;
  kyc: Router;
  buy: Router;
  redeem: Router;
  reserves: Router;
  transactions: Router;
  support: Router;
  admin: Router;
  webhooks: Router;
}

export function createRouters(ctx: HttpContext): AppRouters {
  return {
    health: createHealthRouter(ctx),
    auth: createAuthRouter(ctx),
    wallet: createWalletRouter(ctx),
    kyc: createKycRouter(ctx),
    buy: createBuyRouter(ctx),
    redeem: createRedeemRouter(ctx),
    reserves: createReservesRouter(ctx),
    transactions: createTransactionsRouter(ctx),
    support: createSupportRouter(ctx),
    admin: createAdminRouter(ctx),
    webhooks: createWebhookRouter(ctx)
  };
}
