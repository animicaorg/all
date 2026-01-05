import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { User, AuthTokens, Organization } from '@/types'

interface AuthState {
  user: User | null
  tokens: AuthTokens | null
  organization: Organization | null
  isAuthenticated: boolean
  
  // Actions
  setAuth: (user: User, tokens: AuthTokens) => void
  setOrganization: (org: Organization) => void
  updateTokens: (tokens: AuthTokens) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      tokens: null,
      organization: null,
      isAuthenticated: false,
      
      setAuth: (user, tokens) => set({ user, tokens, isAuthenticated: true }),
      setOrganization: (organization) => set({ organization }),
      updateTokens: (tokens) => set({ tokens }),
      logout: () => set({ user: null, tokens: null, organization: null, isAuthenticated: false }),
    }),
    {
      name: 'animica-auth',
    }
  )
)
