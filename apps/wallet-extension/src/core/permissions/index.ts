// Permission management for dapps

import type { DappPermission } from '../../types/provider';

export class PermissionManager {
  private permissions: Map<string, DappPermission> = new Map();

  constructor(permissions: Record<string, DappPermission> = {}) {
    this.permissions = new Map(Object.entries(permissions));
  }

  hasPermission(origin: string): boolean {
    return this.permissions.has(origin);
  }

  getPermission(origin: string): DappPermission | undefined {
    return this.permissions.get(origin);
  }

  getAllPermissions(): DappPermission[] {
    return Array.from(this.permissions.values());
  }

  grantPermission(origin: string, accounts: string[]): void {
    const now = Date.now();
    this.permissions.set(origin, {
      origin,
      accounts,
      grantedAt: now,
      lastUsedAt: now,
    });
  }

  revokePermission(origin: string): void {
    this.permissions.delete(origin);
  }

  updateLastUsed(origin: string): void {
    const permission = this.permissions.get(origin);
    if (permission) {
      permission.lastUsedAt = Date.now();
    }
  }

  getAuthorizedAccounts(origin: string): string[] {
    const permission = this.permissions.get(origin);
    return permission?.accounts || [];
  }

  toJSON(): Record<string, DappPermission> {
    const obj: Record<string, DappPermission> = {};
    for (const [origin, perm] of this.permissions.entries()) {
      obj[origin] = perm;
    }
    return obj;
  }

  static fromJSON(obj: Record<string, DappPermission>): PermissionManager {
    return new PermissionManager(obj);
  }
}
