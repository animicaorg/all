import { apiClient } from './client'
import type { User, AuthTokens } from '@/types'

export interface RegisterRequest {
  email: string
  password: string
  wallet_address?: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface WalletChallengeRequest {
  wallet_address: string
}

export interface WalletVerifyRequest {
  wallet_address: string
  signature: string
  public_key: string
}

export const authApi = {
  register: async (data: RegisterRequest): Promise<{ user: User; tokens: AuthTokens }> => {
    const response = await apiClient.post('/auth/register', data)
    return response.data
  },
  
  login: async (data: LoginRequest): Promise<{ user: User; tokens: AuthTokens }> => {
    const response = await apiClient.post('/auth/login', data)
    return response.data
  },
  
  getWalletChallenge: async (data: WalletChallengeRequest): Promise<{ challenge: string }> => {
    const response = await apiClient.post('/auth/wallet/challenge', data)
    return response.data
  },
  
  verifyWalletSignature: async (data: WalletVerifyRequest): Promise<{ user: User; tokens: AuthTokens }> => {
    const response = await apiClient.post('/auth/wallet/verify', data)
    return response.data
  },
  
  refreshToken: async (refresh_token: string): Promise<AuthTokens> => {
    const response = await apiClient.post('/auth/refresh', { refresh_token })
    return response.data
  },
  
  logout: async (): Promise<void> => {
    await apiClient.post('/auth/logout')
  },
  
  getCurrentUser: async (): Promise<User> => {
    const response = await apiClient.get('/auth/me')
    return response.data
  },
}
