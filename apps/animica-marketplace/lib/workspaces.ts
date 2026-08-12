// Teams/workspaces RBAC (Pro+; Business = many, for agency/client separation).
// Access is granted ONLY by a WorkspaceMember row — knowing a workspace id grants nothing
// (docs/subscriptions.md security posture). The OWNER row is created in the same transaction
// as the workspace, so every workspace has exactly one OWNER membership.

import { prisma } from './db';
import { ApiError } from './api';

export type WsRole = 'OWNER' | 'ADMIN' | 'MEMBER';

const ROLE_RANK: Record<WsRole, number> = { MEMBER: 0, ADMIN: 1, OWNER: 2 };

export function wsRoleAtLeast(role: WsRole, minRole: WsRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minRole];
}

// Membership row shape shared by the workspace detail + members routes.
export const WS_MEMBER_SELECT = {
  id: true,
  role: true,
  status: true,
  createdAt: true,
  account: { select: { address: true, displayName: true, isAgent: true } },
} as const;

// Throws unless accountId holds at least minRole in the workspace via an ACTIVE membership
// row. No row at all => 404 (not 403), so probing ids never reveals which workspaces exist.
// A row shelved by plan enforcement ('inactive_plan_limit') grants nothing until the owner
// upgrades or frees a seat. Returns the membership row.
export async function requireWorkspaceRole(
  workspaceId: string,
  accountId: string,
  minRole: WsRole,
): Promise<{ role: WsRole; workspaceId: string }> {
  const member = await prisma.workspaceMember.findUnique({
    where: { workspaceId_accountId: { workspaceId, accountId } },
  });
  if (!member) throw new ApiError(404, 'not_found', 'workspace not found');
  if (member.status !== 'active') {
    throw new ApiError(403, 'membership_inactive', 'your membership is inactive — the workspace owner is over their plan limit');
  }
  if (!wsRoleAtLeast(member.role, minRole)) {
    throw new ApiError(403, 'forbidden', `requires workspace ${minRole} role`);
  }
  return member;
}
