import { describe, it, expect } from 'vitest';
import { PermissionManager } from '../src/core/permissions';

describe('Permission Model', () => {
  it('should grant and check permissions', () => {
    const manager = new PermissionManager();

    expect(manager.hasPermission('https://example.com')).toBe(false);

    manager.grantPermission('https://example.com', ['anim1abc']);

    expect(manager.hasPermission('https://example.com')).toBe(true);
    expect(manager.getAuthorizedAccounts('https://example.com')).toEqual(['anim1abc']);
  });

  it('should revoke permissions', () => {
    const manager = new PermissionManager();

    manager.grantPermission('https://example.com', ['anim1abc']);
    expect(manager.hasPermission('https://example.com')).toBe(true);

    manager.revokePermission('https://example.com');
    expect(manager.hasPermission('https://example.com')).toBe(false);
  });

  it('should update last used timestamp', () => {
    const manager = new PermissionManager();

    manager.grantPermission('https://example.com', ['anim1abc']);
    
    const before = manager.getPermission('https://example.com')?.lastUsedAt || 0;
    
    setTimeout(() => {
      manager.updateLastUsed('https://example.com');
      const after = manager.getPermission('https://example.com')?.lastUsedAt || 0;
      
      expect(after).toBeGreaterThanOrEqual(before);
    }, 10);
  });

  it('should serialize and deserialize', () => {
    const manager = new PermissionManager();

    manager.grantPermission('https://example.com', ['anim1abc']);
    manager.grantPermission('https://another.com', ['anim1def', 'anim1ghi']);

    const json = manager.toJSON();
    const restored = PermissionManager.fromJSON(json);

    expect(restored.hasPermission('https://example.com')).toBe(true);
    expect(restored.getAuthorizedAccounts('https://another.com')).toEqual([
      'anim1def',
      'anim1ghi',
    ]);
  });

  it('should not leak permissions between origins', () => {
    const manager = new PermissionManager();

    manager.grantPermission('https://app1.com', ['anim1account1']);
    manager.grantPermission('https://app2.com', ['anim1account2']);

    expect(manager.getAuthorizedAccounts('https://app1.com')).toEqual(['anim1account1']);
    expect(manager.getAuthorizedAccounts('https://app2.com')).toEqual(['anim1account2']);
    expect(manager.getAuthorizedAccounts('https://app3.com')).toEqual([]);
  });
});
