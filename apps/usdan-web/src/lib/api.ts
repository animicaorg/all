import type { BuyIntent, KycStatusResponse, RedemptionRequest, ReserveDashboard, SessionState } from './types';

export class UsdanApi {
  constructor(private readonly baseUrl: string) {}

  private async request<T>(path: string, init: RequestInit = {}, session?: SessionState | null): Promise<T> {
    const headers = new Headers(init.headers ?? {});
    headers.set('Content-Type', 'application/json');
    if (session?.token) headers.set('Authorization', `Bearer ${session.token}`);

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error?.message ?? `Request failed (${response.status})`);
    }
    return payload as T;
  }

  createWalletSession(input: {
    userId: string;
    email?: string;
    walletAddress: string;
    chainId: number;
    message: string;
    signature: string;
  }) {
    return this.request<{ token: string; userId: string; walletAddress: string }>('/auth/wallet/session', {
      method: 'POST',
      body: JSON.stringify(input)
    });
  }

  getKycStatus(session: SessionState) {
    return this.request<KycStatusResponse>('/kyc/status', {}, session);
  }

  createBankAccount(session: SessionState, bankAccountHash: string) {
    return this.request<{ bankAccount: { id: string } }>('/kyc/bank-accounts', {
      method: 'POST',
      body: JSON.stringify({ bankAccountHash, status: 'PENDING_VERIFICATION' })
    }, session);
  }

  listBuyIntents(session: SessionState) {
    return this.request<{ intents: BuyIntent[] }>('/buy/intents', {}, session);
  }

  createBuyIntent(session: SessionState, input: { amountUsd: number; bankAccountId: string; walletAddress: string }) {
    return this.request<{ intent: BuyIntent }>('/buy/intents', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  listRedemptionRequests(session: SessionState) {
    return this.request<{ requests: RedemptionRequest[] }>('/redeem/requests', {}, session);
  }

  createRedemptionRequest(
    session: SessionState,
    input: { amountUsdan: number; bankAccountId: string; walletAddress: string; userIntentHash: string }
  ) {
    return this.request<{ request: RedemptionRequest }>('/redeem/requests', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  getTransactions(session: SessionState) {
    return this.request<{ items: any[] }>('/transactions', {}, session);
  }

  getReserveDashboard() {
    return this.request<{ dashboard: ReserveDashboard }>('/reserves/dashboard');
  }

  getReserveSnapshots() {
    return this.request<{ snapshots: any[] }>('/reserves/snapshots');
  }

  listSupportTickets(session: SessionState) {
    return this.request<{ tickets: any[] }>('/support/tickets', {}, session);
  }

  createSupportTicket(session: SessionState, input: { subject: string; message: string; priority: string }) {
    return this.request<{ ticket: any }>('/support/tickets', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  adminListPurchases(adminKey: string) {
    return this.request<{ purchases: BuyIntent[] }>('/admin/purchases', {
      headers: { 'x-admin-api-key': adminKey }
    });
  }

  adminListRedemptions(adminKey: string) {
    return this.request<{ redemptions: RedemptionRequest[] }>('/admin/redemptions', {
      headers: { 'x-admin-api-key': adminKey }
    });
  }

  adminListWebhooks(adminKey: string) {
    return this.request<{ webhooks: any[] }>('/admin/webhooks', {
      headers: { 'x-admin-api-key': adminKey }
    });
  }

  adminPublishReserveSnapshot(adminKey: string) {
    return this.request<{ snapshot: any }>('/admin/reserves/publish', {
      method: 'POST',
      headers: { 'x-admin-api-key': adminKey }
    });
  }

  adminSetKyc(adminKey: string, input: { userId: string; status: string; provider: string }) {
    return this.request<{ userId: string; status: string }>('/admin/kyc/set-status', {
      method: 'POST',
      headers: { 'x-admin-api-key': adminKey },
      body: JSON.stringify(input)
    });
  }

  adminAddComplianceFlag(adminKey: string, input: { userId: string; type: string; reason: string }) {
    return this.request<{ flag: any }>('/admin/compliance/flags', {
      method: 'POST',
      headers: { 'x-admin-api-key': adminKey },
      body: JSON.stringify(input)
    });
  }
}

const baseUrl = import.meta.env.VITE_USDAN_API_BASE_URL ?? 'http://127.0.0.1:8098';
export const usdanApi = new UsdanApi(baseUrl);
