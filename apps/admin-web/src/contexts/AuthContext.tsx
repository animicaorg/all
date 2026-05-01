/**
 * Authentication Context
 * Manages authentication state and provides auth methods
 */

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient, type LoginRequest, type AdminRole } from '../services/api';

export type { AdminRole };
export type Permission =
  | 'users:read'
  | 'users:freeze'
  | 'kyc:read'
  | 'kyc:review'
  | 'markets:read'
  | 'markets:write'
  | 'markets:halt'
  | 'fees:read'
  | 'fees:write'
  | 'withdrawals:read'
  | 'withdrawals:approve'
  | 'withdrawals:sign'
  | 'incidents:read'
  | 'incidents:execute'
  | 'audit:read'
  | 'wallets:read'
  | 'wallets:write';

const rolePermissions: Record<AdminRole, Permission[]> = {
  SUPERADMIN: [
    'users:read',
    'users:freeze',
    'kyc:read',
    'kyc:review',
    'markets:read',
    'markets:write',
    'markets:halt',
    'fees:read',
    'fees:write',
    'withdrawals:read',
    'withdrawals:approve',
    'withdrawals:sign',
    'incidents:read',
    'incidents:execute',
    'audit:read',
    'wallets:read',
    'wallets:write',
  ],
  OPS: [
    'users:read',
    'users:freeze',
    'kyc:read',
    'markets:read',
    'markets:write',
    'markets:halt',
    'fees:read',
    'fees:write',
    'withdrawals:read',
    'withdrawals:approve',
    'withdrawals:sign',
    'incidents:read',
    'incidents:execute',
    'audit:read',
    'wallets:read',
    'wallets:write',
  ],
  COMPLIANCE: [
    'users:read',
    'users:freeze',
    'kyc:read',
    'kyc:review',
    'withdrawals:read',
    'withdrawals:approve',
    'incidents:read',
    'audit:read',
  ],
  SUPPORT: ['users:read', 'kyc:read', 'markets:read', 'withdrawals:read', 'audit:read', 'wallets:read'],
  READONLY: [
    'users:read',
    'kyc:read',
    'markets:read',
    'fees:read',
    'withdrawals:read',
    'incidents:read',
    'audit:read',
    'wallets:read',
  ],
};

export interface Admin {
  id: string;
  email: string;
  role: AdminRole;
  status: string;
  lastLoginAt: string | null;
  createdAt: string;
  updatedAt: string;
}

interface AuthContextValue {
  admin: Admin | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (credentials: LoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  hasPermission: (permission: Permission) => boolean;
  hasRole: (...roles: AdminRole[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [admin, setAdmin] = useState<Admin | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();

  // Check authentication on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        apiClient.loadToken();
        const response = await apiClient.me();
        setAdmin(response.data.admin);
      } catch (error) {
        apiClient.clearToken();
      } finally {
        setIsLoading(false);
      }
    };

    checkAuth();
  }, []);

  const login = async (credentials: LoginRequest) => {
    const response = await apiClient.login(credentials);
    setAdmin(response.data.admin);
    if (response.data.bootstrapCreated) {
      localStorage.setItem('admin_bootstrap_created', 'true');
    }
    navigate('/');
  };

  const logout = async () => {
    await apiClient.logout();
    setAdmin(null);
    navigate('/login');
  };

  const hasPermission = (permission: Permission): boolean => {
    if (!admin) return false;
    return rolePermissions[admin.role]?.includes(permission) ?? false;
  };

  const hasRole = (...roles: AdminRole[]): boolean => {
    if (!admin) return false;
    return roles.includes(admin.role);
  };

  return (
    <AuthContext.Provider
      value={{
        admin,
        isAuthenticated: !!admin,
        isLoading,
        login,
        logout,
        hasPermission,
        hasRole,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
