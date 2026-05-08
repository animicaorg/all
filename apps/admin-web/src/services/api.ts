/**
 * API Client
 * Axios-based API client with authentication.
 */

import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios';

export interface ApiError {
  error: string;
  message: string;
  details?: unknown;
  requestId?: string;
}

export interface ApiResponse<T> {
  success: boolean;
  data: T;
}

export interface Pagination {
  page: number;
  limit: number;
  total: number;
  totalPages: number;
}

export interface LoginRequest {
  email: string;
  password: string;
  totpToken?: string;
  bootstrapSecret?: string;
}

export type AdminRole = 'SUPERADMIN' | 'OPS' | 'COMPLIANCE' | 'SUPPORT' | 'READONLY';

export interface Admin {
  id: string;
  email: string;
  role: AdminRole;
  status: string;
  lastLoginAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface LoginResponse {
  success: boolean;
  data: {
    admin: Admin;
    accessToken: string;
    refreshToken: string;
    sessionId: string;
    bootstrapCreated?: boolean;
  };
}

export interface MeResponse {
  success: boolean;
  data: {
    admin: Admin;
    session: {
      id: string;
      adminId: string;
    };
  };
}

export interface UserProfile {
  userId: string;
  displayName: string | null;
  country: string | null;
  region: string | null;
  legalName: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface UserSummary {
  id: string;
  email: string | null;
  status: 'ACTIVE' | 'SUSPENDED' | 'CLOSED';
  role: string;
  twofaEnabled: boolean;
  createdAt: string;
  updatedAt: string;
  balanceTotals?: BalanceTotal[];
}

export interface RiskFlag {
  id: string;
  userId: string;
  code: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  note: string | null;
  status: 'OPEN' | 'CLOSED';
  createdAt: string;
  closedAt: string | null;
}

export interface BalanceSummary {
  asset: string;
  available: string;
  locked: string;
  total: string;
}

export interface BalanceTotal {
  asset: string;
  total: string;
}

export interface UserDetail extends UserSummary {
  profile: UserProfile | null;
  kycCases: KycCase[];
  riskFlags: RiskFlag[];
}

export interface UsersListData {
  users: UserSummary[];
  pagination: Pagination;
}

export interface UserDetailData {
  user: UserDetail;
  balances: BalanceSummary[];
  stats: {
    recentOrders: number;
  };
}

export interface Asset {
  id: string;
  symbol: string;
  name: string;
  decimals: number;
  kind: string;
  isEnabled: boolean;
  createdAt: string;
}

export interface Network {
  id: string;
  code: string;
  kind: string;
  chainId: string | null;
  rpcUrl: string | null;
  confirmationsRequired: number;
  createdAt: string;
}

export type WalletProvider = 'BITGO' | 'ANIMICA_NODE' | 'BITCOIN_NODE' | 'LOCAL_ANIMICA' | 'OTHER';

export interface AssetNetwork {
  id: string;
  assetId: string;
  networkId: string;
  contractAddress: string | null;
  provider: WalletProvider;
  bitgoCoin: string | null;
  rpcUrl: string | null;
  depositEnabled: boolean;
  withdrawalEnabled: boolean;
  minWithdrawal: string;
  withdrawalFee: string;
  asset: Asset;
  network: Network;
  _count?: {
    depositAddresses: number;
    deposits: number;
    withdrawals: number;
  };
}

export interface KycDocument {
  id: string;
  kycCaseId?: string;
  docType: string;
  storageRef: string;
  sha256: string;
  createdAt: string;
}

export interface KycCase {
  id: string;
  userId: string;
  provider: 'BITGO' | 'SUMSUB' | 'MANUAL' | 'NONE';
  status: 'NOT_STARTED' | 'PENDING' | 'VERIFIED' | 'REJECTED' | 'REVIEW';
  riskTier: 'LOW' | 'MEDIUM' | 'HIGH' | null;
  submittedAt: string | null;
  reviewedAt: string | null;
  reviewerUserId: string | null;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
  user: (UserSummary & { profile?: UserProfile | null }) | null;
  documents: KycDocument[];
}

export interface KycListData {
  cases: KycCase[];
  queueCounts: Array<{ status: string; count: number }>;
  pagination: Pagination;
}

export interface MarketControl {
  id: string;
  marketId: string;
  tradingEnabled: boolean;
  depositsEnabled: boolean;
  withdrawalsEnabled: boolean;
  reason: string | null;
  updatedBy: string;
  updatedAt: string;
}

export interface Market {
  id: string;
  symbol: string;
  status: 'ONLINE' | 'HALTED' | 'READONLY';
  priceTick: string;
  sizeStep: string;
  minOrderSize: string;
  makerFeeBps: number;
  takerFeeBps: number;
  feeAsset: string;
  createdAt: string;
  baseAsset: Asset;
  quoteAsset: Asset;
  marketControl: MarketControl | null;
  _count?: {
    orders: number;
    trades: number;
  };
}

export interface MarketsListData {
  markets: Market[];
  pagination: Pagination;
}

export interface MarketAssetOption {
  symbol: string;
  name: string;
  decimals: number;
  sources: string[];
  networks: string[];
  enabled: boolean;
}

export interface MarketAssetsData {
  assets: MarketAssetOption[];
}

export interface CreateMarketRequest {
  symbol?: string;
  baseAsset: string;
  quoteAsset: string;
  priceTick: string;
  sizeStep: string;
  minOrderSize: string;
  makerFeeBps: number | string;
  takerFeeBps: number | string;
  feeAsset?: string;
  status: Market['status'];
}

export interface FeeSchedule {
  id: string;
  scope: 'GLOBAL' | 'USER_TIER' | 'MARKET';
  name: string | null;
  userId: string | null;
  marketId: string | null;
  makerBps: number;
  takerBps: number;
  withdrawalFeeOverride: string | null;
  rulesJson: unknown;
  status: string;
  effectiveFrom: string;
  effectiveTo: string | null;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
  market?: Pick<Market, 'id' | 'symbol'> | null;
  creator?: Pick<Admin, 'id' | 'email'> | null;
}

export interface FeesListData {
  fees: FeeSchedule[];
  markets: Array<Pick<Market, 'id' | 'symbol'>>;
  pagination: Pagination;
}

export interface Wallet {
  id: string;
  networkId: string;
  assetNetworkId: string;
  purpose: 'HOT' | 'WARM' | 'COLD' | 'TREASURY' | 'FEE' | string;
  provider: WalletProvider;
  providerRef: string;
  address: string | null;
  isActive: boolean;
  createdAt: string;
  network: Network;
  _count?: {
    assignedAddresses: number;
  };
}

export interface WalletsListData {
  wallets: Wallet[];
  assetNetworks: AssetNetwork[];
  assets: Asset[];
  networks: Network[];
  pagination: Pagination;
}

export interface ProviderSetupRequest {
  assetNetworkId: string;
  provider: Extract<WalletProvider, 'BITGO' | 'ANIMICA_NODE' | 'BITCOIN_NODE'>;
  walletId?: string;
  assetName?: string | null;
  address?: string | null;
  rpcUrl?: string | null;
  bitgoCoin?: string | null;
  depositEnabled?: boolean;
  withdrawalEnabled?: boolean;
}

export interface WithdrawalApproval {
  id: string;
  withdrawalId: string;
  approverUserId: string | null;
  approverAdminId: string | null;
  action: 'APPROVE' | 'REJECT';
  note: string | null;
  createdAt: string;
  approverAdmin?: Pick<Admin, 'id' | 'email' | 'role'> | null;
  approverUser?: Pick<UserSummary, 'id' | 'email'> | null;
}

export interface Withdrawal {
  id: string;
  userId: string;
  assetNetworkId: string;
  destinationAddress: string;
  destinationTag: string | null;
  amount: string;
  feeAmount: string;
  status:
    | 'REQUESTED'
    | 'RISK_REVIEW'
    | 'APPROVED'
    | 'SIGNING'
    | 'BROADCAST'
    | 'CONFIRMED'
    | 'FAILED'
    | 'CANCELED';
  requestedAt: string;
  approvedAt: string | null;
  broadcastAt: string | null;
  confirmedAt: string | null;
  txid: string | null;
  provider: 'BITGO' | 'ANIMICA_NODE' | 'BITCOIN_NODE' | 'MANUAL';
  providerRef: string | null;
  idempotencyKey: string | null;
  riskScore: string | null;
  createdAt: string;
  updatedAt: string;
  user: Pick<UserSummary, 'id' | 'email' | 'status'>;
  assetNetwork: AssetNetwork;
  approvals: WithdrawalApproval[];
}

export interface WithdrawalsListData {
  withdrawals: Withdrawal[];
  statusCounts: Array<{ status: string; count: number }>;
  pagination: Pagination;
}

export interface IncidentAction {
  id: string;
  incidentId: string;
  action: string;
  payload: unknown;
  status: string;
  createdBy: string;
  createdAt: string;
  completedAt: string | null;
  creator?: Pick<Admin, 'id' | 'email' | 'role'>;
}

export interface Incident {
  id: string;
  title: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  status: 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CLOSED';
  createdBy: string;
  createdAt: string;
  resolvedAt: string | null;
  closedAt: string | null;
  creator?: Pick<Admin, 'id' | 'email' | 'role'>;
  actions: IncidentAction[];
}

export interface IncidentsListData {
  incidents: Incident[];
  statusCounts: Array<{ status: string; count: number }>;
  pagination: Pagination;
}

export interface AuditLog {
  id: string;
  actorUserId: string | null;
  actorAdminId: string | null;
  actorType: 'USER' | 'ADMIN' | 'SYSTEM';
  action: string;
  entityType: string;
  entityId: string | null;
  requestId: string | null;
  ip: string | null;
  userAgent: string | null;
  before: unknown;
  after: unknown;
  metadata: unknown;
  createdAt: string;
  actorAdmin?: Pick<Admin, 'id' | 'email' | 'role'> | null;
  actor?: Pick<UserSummary, 'id' | 'email' | 'role'> | null;
}

export interface AuditListData {
  logs: AuditLog[];
  pagination: Pagination;
}

export interface OverviewData {
  metrics: {
    users: { total: number; active: number; new24h: number };
    kyc: { pending: number };
    withdrawals: {
      pending: number;
      last30dByStatus: Array<{ status: string; count: number }>;
    };
    incidents: { open: number };
    markets: { total: number; halted: number };
    trades: { last24h: number };
  };
  recentAudit: Array<{
    id: string;
    actorType: string;
    actor: string;
    action: string;
    entityType: string;
    entityId: string | null;
    createdAt: string;
  }>;
}

export interface HealthData {
  status: string;
  service: string;
  timestamp: string;
  checks: Record<string, { status: string; count?: number; message?: string }>;
}

export interface BitgoSettings {
  id: string;
  environment: 'test' | 'prod';
  baseUrl: string | null;
  wallets: Record<string, string> | null;
  coins: Record<string, unknown> | null;
  enabled: boolean;
  accessTokenMasked: string | null;
  webhookSecretMasked: string | null;
  updatedAt: string | null;
}

export interface BitgoTestResponse {
  ok: boolean;
  message: string;
}

class ApiClient {
  private client: AxiosInstance;
  private accessToken: string | null = null;

  constructor() {
    this.client = axios.create({
      baseURL: '/admin/v1',
      withCredentials: true,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    this.client.interceptors.request.use(
      (config) => {
        if (this.accessToken) {
          config.headers.Authorization = `Bearer ${this.accessToken}`;
        }
        return config;
      },
      (error) => Promise.reject(error)
    );

    this.client.interceptors.response.use(
      (response) => response,
      async (error) => {
        if (error.response?.status === 401 && !error.config?._retry) {
          error.config._retry = true;
          const refreshed = await this.tryRefreshToken();
          if (refreshed && error.config) {
            return this.client.request(error.config);
          }
          this.clearToken();
        }
        return Promise.reject(error);
      }
    );
  }

  setToken(token: string) {
    this.accessToken = token;
    localStorage.setItem('admin_token', token);
  }

  clearToken() {
    this.accessToken = null;
    localStorage.removeItem('admin_token');
    localStorage.removeItem('admin_refresh_token');
    localStorage.removeItem('admin_session_id');
  }

  loadToken() {
    const token = localStorage.getItem('admin_token');
    if (token) {
      this.accessToken = token;
    }
  }

  async login(credentials: LoginRequest): Promise<LoginResponse> {
    const response = await this.client.post<LoginResponse>('/auth/login', credentials);
    const { accessToken, refreshToken, sessionId } = response.data.data;

    this.setToken(accessToken);
    localStorage.setItem('admin_refresh_token', refreshToken);
    localStorage.setItem('admin_session_id', sessionId);

    return response.data;
  }

  async logout(): Promise<void> {
    try {
      await this.client.post('/auth/logout');
    } finally {
      this.clearToken();
    }
  }

  async me(): Promise<MeResponse> {
    const response = await this.client.get<MeResponse>('/auth/me');
    return response.data;
  }

  async getHealth(): Promise<HealthData> {
    const response = await this.client.get<HealthData>('/health');
    return response.data;
  }

  async getOverview(): Promise<ApiResponse<OverviewData>> {
    const response = await this.client.get<ApiResponse<OverviewData>>('/overview');
    return response.data;
  }

  async listUsers(params: Record<string, unknown>): Promise<ApiResponse<UsersListData>> {
    const response = await this.client.get<ApiResponse<UsersListData>>('/users', { params });
    return response.data;
  }

  async getUser(id: string): Promise<ApiResponse<UserDetailData>> {
    const response = await this.client.get<ApiResponse<UserDetailData>>(`/users/${id}`);
    return response.data;
  }

  async freezeUser(id: string, reason: string): Promise<ApiResponse<{ user: UserSummary }>> {
    const response = await this.client.post<ApiResponse<{ user: UserSummary }>>(`/users/${id}/freeze`, {
      reason,
    });
    return response.data;
  }

  async unfreezeUser(id: string): Promise<ApiResponse<{ user: UserSummary }>> {
    const response = await this.client.post<ApiResponse<{ user: UserSummary }>>(`/users/${id}/unfreeze`);
    return response.data;
  }

  async listKyc(params: Record<string, unknown>): Promise<ApiResponse<KycListData>> {
    const response = await this.client.get<ApiResponse<KycListData>>('/kyc', { params });
    return response.data;
  }

  async reviewKyc(
    id: string,
    payload: { action: 'approve' | 'reject' | 'request_info'; notes?: string; riskTier?: string }
  ): Promise<ApiResponse<{ case: KycCase }>> {
    const response = await this.client.patch<ApiResponse<{ case: KycCase }>>(`/kyc/${id}/review`, payload);
    return response.data;
  }

  async listMarkets(params: Record<string, unknown>): Promise<ApiResponse<MarketsListData>> {
    const response = await this.client.get<ApiResponse<MarketsListData>>('/markets', { params });
    return response.data;
  }

  async listMarketAssets(): Promise<ApiResponse<MarketAssetsData>> {
    const response = await this.client.get<ApiResponse<MarketAssetsData>>('/markets/assets');
    return response.data;
  }

  async createMarket(payload: CreateMarketRequest): Promise<ApiResponse<{ market: Market }>> {
    const response = await this.client.post<ApiResponse<{ market: Market }>>('/markets', payload);
    return response.data;
  }

  async updateMarketStatus(
    id: string,
    payload: { status: Market['status']; reason?: string }
  ): Promise<ApiResponse<{ market: Market }>> {
    const response = await this.client.patch<ApiResponse<{ market: Market }>>(`/markets/${id}/status`, payload);
    return response.data;
  }

  async updateMarketControls(
    id: string,
    payload: Pick<MarketControl, 'tradingEnabled' | 'depositsEnabled' | 'withdrawalsEnabled'> & {
      reason?: string | null;
    }
  ): Promise<ApiResponse<{ control: MarketControl }>> {
    const response = await this.client.put<ApiResponse<{ control: MarketControl }>>(
      `/markets/${id}/controls`,
      payload
    );
    return response.data;
  }

  async cancelOpenOrders(id: string): Promise<ApiResponse<{ canceledOrders: number }>> {
    const response = await this.client.post<ApiResponse<{ canceledOrders: number }>>(
      `/markets/${id}/cancel-open-orders`
    );
    return response.data;
  }

  async listFees(params: Record<string, unknown>): Promise<ApiResponse<FeesListData>> {
    const response = await this.client.get<ApiResponse<FeesListData>>('/fees', { params });
    return response.data;
  }

  async createFee(
    payload: Omit<Partial<FeeSchedule>, 'id' | 'createdAt' | 'updatedAt' | 'creator' | 'market'> &
      Pick<FeeSchedule, 'scope' | 'makerBps' | 'takerBps' | 'effectiveFrom'>
  ): Promise<ApiResponse<{ fee: FeeSchedule }>> {
    const response = await this.client.post<ApiResponse<{ fee: FeeSchedule }>>('/fees', payload);
    return response.data;
  }

  async updateFee(id: string, payload: Partial<FeeSchedule>): Promise<ApiResponse<{ fee: FeeSchedule }>> {
    const response = await this.client.patch<ApiResponse<{ fee: FeeSchedule }>>(`/fees/${id}`, payload);
    return response.data;
  }

  async archiveFee(id: string): Promise<ApiResponse<{ fee: FeeSchedule }>> {
    const response = await this.client.delete<ApiResponse<{ fee: FeeSchedule }>>(`/fees/${id}`);
    return response.data;
  }

  async listWallets(params: Record<string, unknown>): Promise<ApiResponse<WalletsListData>> {
    const response = await this.client.get<ApiResponse<WalletsListData>>('/wallets', { params });
    return response.data;
  }

  async updateWallet(id: string, payload: Partial<Pick<Wallet, 'providerRef' | 'address' | 'isActive'>>) {
    const response = await this.client.patch<ApiResponse<{ wallet: Wallet }>>(`/wallets/${id}`, payload);
    return response.data;
  }

  async configureWalletProvider(
    payload: ProviderSetupRequest
  ): Promise<ApiResponse<{ wallet: Wallet | null; assetNetwork: AssetNetwork }>> {
    const response = await this.client.put<ApiResponse<{ wallet: Wallet | null; assetNetwork: AssetNetwork }>>(
      '/wallets/provider-setup',
      payload
    );
    return response.data;
  }

  async updateAssetNetwork(
    id: string,
    payload: Partial<Pick<AssetNetwork, 'depositEnabled' | 'withdrawalEnabled' | 'minWithdrawal' | 'withdrawalFee'>>
  ) {
    const response = await this.client.patch<ApiResponse<{ assetNetwork: AssetNetwork }>>(
      `/wallets/asset-networks/${id}`,
      payload
    );
    return response.data;
  }

  async listWithdrawals(params: Record<string, unknown>): Promise<ApiResponse<WithdrawalsListData>> {
    const response = await this.client.get<ApiResponse<WithdrawalsListData>>('/withdrawals', { params });
    return response.data;
  }

  async approveWithdrawal(id: string, note?: string): Promise<ApiResponse<{ withdrawal: Withdrawal }>> {
    const response = await this.client.post<ApiResponse<{ withdrawal: Withdrawal }>>(`/withdrawals/${id}/approve`, {
      note,
    });
    return response.data;
  }

  async rejectWithdrawal(id: string, note: string): Promise<ApiResponse<{ withdrawal: Withdrawal }>> {
    const response = await this.client.post<ApiResponse<{ withdrawal: Withdrawal }>>(`/withdrawals/${id}/reject`, {
      note,
    });
    return response.data;
  }

  async retryWithdrawal(id: string, note?: string): Promise<ApiResponse<{ withdrawal: Withdrawal }>> {
    const response = await this.client.post<ApiResponse<{ withdrawal: Withdrawal }>>(`/withdrawals/${id}/retry`, {
      note,
    });
    return response.data;
  }

  async listIncidents(params: Record<string, unknown>): Promise<ApiResponse<IncidentsListData>> {
    const response = await this.client.get<ApiResponse<IncidentsListData>>('/incidents', { params });
    return response.data;
  }

  async createIncident(payload: Pick<Incident, 'title' | 'severity'>): Promise<ApiResponse<{ incident: Incident }>> {
    const response = await this.client.post<ApiResponse<{ incident: Incident }>>('/incidents', payload);
    return response.data;
  }

  async updateIncident(id: string, payload: Partial<Pick<Incident, 'title' | 'severity' | 'status'>>) {
    const response = await this.client.patch<ApiResponse<{ incident: Incident }>>(`/incidents/${id}`, payload);
    return response.data;
  }

  async addIncidentAction(
    id: string,
    payload: { action: string; status?: string; payload?: unknown }
  ): Promise<ApiResponse<{ action: IncidentAction }>> {
    const response = await this.client.post<ApiResponse<{ action: IncidentAction }>>(
      `/incidents/${id}/actions`,
      payload
    );
    return response.data;
  }

  async listAudit(params: Record<string, unknown>): Promise<ApiResponse<AuditListData>> {
    const response = await this.client.get<ApiResponse<AuditListData>>('/audit', { params });
    return response.data;
  }

  async getBitgoSettings(): Promise<ApiResponse<BitgoSettings>> {
    const response = await this.client.get<ApiResponse<BitgoSettings>>('/settings/bitgo');
    return response.data;
  }

  async updateBitgoSettings(payload: {
    environment: 'test' | 'prod';
    baseUrl?: string | null;
    accessToken?: string | null;
    webhookSecret?: string | null;
    wallets?: Record<string, string> | null;
    coins?: Record<string, unknown> | null;
    enabled: boolean;
  }): Promise<ApiResponse<BitgoSettings>> {
    const response = await this.client.put<ApiResponse<BitgoSettings>>('/settings/bitgo', payload);
    return response.data;
  }

  async testBitgoConnection(): Promise<ApiResponse<BitgoTestResponse>> {
    const response = await this.client.post<ApiResponse<BitgoTestResponse>>('/settings/bitgo/test');
    return response.data;
  }

  private async tryRefreshToken(): Promise<boolean> {
    try {
      const refreshToken = localStorage.getItem('admin_refresh_token');
      const sessionId = localStorage.getItem('admin_session_id');

      if (!refreshToken || !sessionId) {
        return false;
      }

      const response = await axios.post('/admin/v1/auth/refresh', {
        refreshToken,
        sessionId,
      });

      const { accessToken } = response.data.data;
      this.setToken(accessToken);
      return true;
    } catch {
      return false;
    }
  }

  async get<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.get<T>(url, config);
    return response.data;
  }

  async post<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.post<T>(url, data, config);
    return response.data;
  }

  async patch<T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.patch<T>(url, data, config);
    return response.data;
  }

  async delete<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.delete<T>(url, config);
    return response.data;
  }
}

export const apiClient = new ApiClient();
