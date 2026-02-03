/**
 * RBAC (Role-Based Access Control) Middleware
 * Enforces permission-based access to endpoints
 */

import type { Request, Response, NextFunction } from 'express';
import type { AdminRole } from '@prisma/client';

/**
 * Permission definitions
 */
export const PERMISSIONS = {
  // User management
  USERS_READ: 'users:read',
  USERS_WRITE: 'users:write',
  USERS_FREEZE: 'users:freeze',
  
  // KYC management
  KYC_READ: 'kyc:read',
  KYC_REVIEW: 'kyc:review',
  
  // Market management
  MARKETS_READ: 'markets:read',
  MARKETS_WRITE: 'markets:write',
  MARKETS_HALT: 'markets:halt',
  
  // Fee management
  FEES_READ: 'fees:read',
  FEES_WRITE: 'fees:write',
  
  // Withdrawal management
  WITHDRAWALS_READ: 'withdrawals:read',
  WITHDRAWALS_APPROVE: 'withdrawals:approve',
  WITHDRAWALS_SIGN: 'withdrawals:sign',
  
  // Incident management
  INCIDENTS_READ: 'incidents:read',
  INCIDENTS_EXECUTE: 'incidents:execute',
  
  // Audit logs
  AUDIT_READ: 'audit:read',
  
  // Admin management
  ADMINS_READ: 'admins:read',
  ADMINS_WRITE: 'admins:write',
  
  // Wallet visibility
  WALLETS_READ: 'wallets:read',
  WALLETS_WRITE: 'wallets:write',
} as const;

export type Permission = typeof PERMISSIONS[keyof typeof PERMISSIONS];

/**
 * Role to permissions mapping
 */
const ROLE_PERMISSIONS: Record<AdminRole, Permission[]> = {
  SUPERADMIN: Object.values(PERMISSIONS),
  
  OPS: [
    PERMISSIONS.USERS_READ,
    PERMISSIONS.USERS_WRITE,
    PERMISSIONS.USERS_FREEZE,
    PERMISSIONS.KYC_READ,
    PERMISSIONS.MARKETS_READ,
    PERMISSIONS.MARKETS_WRITE,
    PERMISSIONS.MARKETS_HALT,
    PERMISSIONS.FEES_READ,
    PERMISSIONS.FEES_WRITE,
    PERMISSIONS.WITHDRAWALS_READ,
    PERMISSIONS.WITHDRAWALS_APPROVE,
    PERMISSIONS.WITHDRAWALS_SIGN,
    PERMISSIONS.INCIDENTS_READ,
    PERMISSIONS.INCIDENTS_EXECUTE,
    PERMISSIONS.AUDIT_READ,
    PERMISSIONS.WALLETS_READ,
    PERMISSIONS.WALLETS_WRITE,
  ],
  
  COMPLIANCE: [
    PERMISSIONS.USERS_READ,
    PERMISSIONS.USERS_FREEZE,
    PERMISSIONS.KYC_READ,
    PERMISSIONS.KYC_REVIEW,
    PERMISSIONS.WITHDRAWALS_READ,
    PERMISSIONS.WITHDRAWALS_APPROVE,
    PERMISSIONS.INCIDENTS_READ,
    PERMISSIONS.AUDIT_READ,
  ],
  
  SUPPORT: [
    PERMISSIONS.USERS_READ,
    PERMISSIONS.KYC_READ,
    PERMISSIONS.MARKETS_READ,
    PERMISSIONS.WITHDRAWALS_READ,
    PERMISSIONS.AUDIT_READ,
    PERMISSIONS.WALLETS_READ,
  ],
  
  READONLY: [
    PERMISSIONS.USERS_READ,
    PERMISSIONS.KYC_READ,
    PERMISSIONS.MARKETS_READ,
    PERMISSIONS.FEES_READ,
    PERMISSIONS.WITHDRAWALS_READ,
    PERMISSIONS.INCIDENTS_READ,
    PERMISSIONS.AUDIT_READ,
    PERMISSIONS.WALLETS_READ,
  ],
};

/**
 * Check if admin has permission
 */
export function hasPermission(role: AdminRole, permission: Permission): boolean {
  return ROLE_PERMISSIONS[role]?.includes(permission) ?? false;
}

/**
 * Middleware factory to require specific permission
 */
export function requirePermission(...permissions: Permission[]) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const admin = req.admin;

    if (!admin) {
      res.status(401).json({ error: 'Unauthorized', message: 'Authentication required' });
      return;
    }

    const hasRequiredPermission = permissions.some((permission) =>
      hasPermission(admin.role, permission)
    );

    if (!hasRequiredPermission) {
      res.status(403).json({
        error: 'Forbidden',
        message: 'Insufficient permissions',
        required: permissions,
      });
      return;
    }

    next();
  };
}

/**
 * Middleware to require specific role
 */
export function requireRole(...roles: AdminRole[]) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const admin = req.admin;

    if (!admin) {
      res.status(401).json({ error: 'Unauthorized', message: 'Authentication required' });
      return;
    }

    if (!roles.includes(admin.role)) {
      res.status(403).json({
        error: 'Forbidden',
        message: 'Insufficient role',
        required: roles,
        current: admin.role,
      });
      return;
    }

    next();
  };
}
