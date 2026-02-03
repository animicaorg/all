/**
 * Authentication Context
 * Manages authentication state and provides auth methods
 */

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient, type LoginRequest } from '../services/api';

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

interface AuthContextValue {
  admin: Admin | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (credentials: LoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  hasPermission: (permission: string) => boolean;
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

  const hasPermission = (permission: string): boolean => {
    if (!admin) return false;
    // SUPERADMIN has all permissions
    if (admin.role === 'SUPERADMIN') return true;
    
    // Implement role-permission mapping (simplified)
    // In production, this should match the backend RBAC
    return true; // TODO: Implement proper permission checking
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
