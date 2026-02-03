/**
 * API Client
 * Axios-based API client with authentication
 */

import axios, { type AxiosInstance } from 'axios';

export interface ApiError {
  error: string;
  message: string;
  details?: any;
  requestId?: string;
}

export interface LoginRequest {
  email: string;
  password: string;
  totpToken?: string;
  bootstrapSecret?: string;
}

export interface LoginResponse {
  success: boolean;
  data: {
    admin: {
      id: string;
      email: string;
      role: string;
      status: string;
    };
    accessToken: string;
    refreshToken: string;
    sessionId: string;
    bootstrapCreated?: boolean;
  };
}

export interface MeResponse {
  success: boolean;
  data: {
    admin: {
      id: string;
      email: string;
      role: string;
      status: string;
      lastLoginAt: string | null;
      createdAt: string;
      updatedAt: string;
    };
    session: {
      id: string;
      adminId: string;
    };
  };
}

export interface BitgoSettings {
  id: string;
  environment: 'test' | 'prod';
  baseUrl: string | null;
  wallets: Record<string, string> | null;
  coins: Record<string, any> | null;
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

    // Request interceptor to add auth token
    this.client.interceptors.request.use(
      (config) => {
        if (this.accessToken) {
          config.headers.Authorization = `Bearer ${this.accessToken}`;
        }
        return config;
      },
      (error) => Promise.reject(error)
    );

    // Response interceptor for error handling
    this.client.interceptors.response.use(
      (response) => response,
      async (error) => {
        if (error.response?.status === 401) {
          // Try to refresh token
          const refreshed = await this.tryRefreshToken();
          if (refreshed && error.config) {
            // Retry original request
            return this.client.request(error.config);
          }
          // Clear token and redirect to login
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

  async getBitgoSettings(): Promise<{ success: boolean; data: BitgoSettings }> {
    const response = await this.client.get<{ success: boolean; data: BitgoSettings }>(
      '/settings/bitgo'
    );
    return response.data;
  }

  async updateBitgoSettings(
    payload: {
      environment: 'test' | 'prod';
      baseUrl?: string | null;
      accessToken?: string | null;
      webhookSecret?: string | null;
      wallets?: Record<string, string> | null;
      coins?: Record<string, any> | null;
      enabled: boolean;
    }
  ): Promise<{ success: boolean; data: BitgoSettings }> {
    const response = await this.client.put<{ success: boolean; data: BitgoSettings }>(
      '/settings/bitgo',
      payload
    );
    return response.data;
  }

  async testBitgoConnection(): Promise<{ success: boolean; data: BitgoTestResponse }> {
    const response = await this.client.post<{ success: boolean; data: BitgoTestResponse }>(
      '/settings/bitgo/test'
    );
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

  // Generic request methods
  async get<T = any>(url: string, config?: any): Promise<T> {
    const response = await this.client.get<T>(url, config);
    return response.data;
  }

  async post<T = any>(url: string, data?: any, config?: any): Promise<T> {
    const response = await this.client.post<T>(url, data, config);
    return response.data;
  }

  async patch<T = any>(url: string, data?: any, config?: any): Promise<T> {
    const response = await this.client.patch<T>(url, data, config);
    return response.data;
  }

  async delete<T = any>(url: string, config?: any): Promise<T> {
    const response = await this.client.delete<T>(url, config);
    return response.data;
  }
}

export const apiClient = new ApiClient();
