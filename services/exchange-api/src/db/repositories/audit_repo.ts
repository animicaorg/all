/**
 * Audit Log Repository
 * Data access layer for audit logging
 */

import type { PrismaClient, AuditLog, AuditActorType } from '@prisma/client';

export interface CreateAuditLogInput {
  actorUserId?: string;
  actorType: AuditActorType;
  action: string;
  entityType: string;
  entityId?: string;
  ip?: string;
  userAgent?: string;
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
}

export class AuditRepository {
  constructor(private prisma: PrismaClient) {}

  /**
   * Create an audit log entry
   */
  async log(input: CreateAuditLogInput): Promise<AuditLog> {
    return this.prisma.auditLog.create({
      data: {
        actorUserId: input.actorUserId,
        actorType: input.actorType,
        action: input.action,
        entityType: input.entityType,
        entityId: input.entityId,
        ip: input.ip,
        userAgent: input.userAgent,
        before: input.before,
        after: input.after,
      },
    });
  }

  /**
   * Get audit logs for an entity
   */
  async getLogsForEntity(
    entityType: string,
    entityId: string,
    limit = 50
  ): Promise<AuditLog[]> {
    return this.prisma.auditLog.findMany({
      where: {
        entityType,
        entityId,
      },
      orderBy: {
        createdAt: 'desc',
      },
      take: limit,
    });
  }

  /**
   * Get audit logs for a user
   */
  async getLogsForUser(userId: string, limit = 50): Promise<AuditLog[]> {
    return this.prisma.auditLog.findMany({
      where: {
        actorUserId: userId,
      },
      orderBy: {
        createdAt: 'desc',
      },
      take: limit,
    });
  }

  /**
   * Search audit logs
   */
  async searchLogs(filters: {
    actorUserId?: string;
    actorType?: AuditActorType;
    action?: string;
    entityType?: string;
    startDate?: Date;
    endDate?: Date;
    limit?: number;
  }): Promise<AuditLog[]> {
    const where: any = {};

    if (filters.actorUserId) where.actorUserId = filters.actorUserId;
    if (filters.actorType) where.actorType = filters.actorType;
    if (filters.action) where.action = filters.action;
    if (filters.entityType) where.entityType = filters.entityType;

    if (filters.startDate || filters.endDate) {
      where.createdAt = {};
      if (filters.startDate) where.createdAt.gte = filters.startDate;
      if (filters.endDate) where.createdAt.lte = filters.endDate;
    }

    return this.prisma.auditLog.findMany({
      where,
      orderBy: {
        createdAt: 'desc',
      },
      take: filters.limit || 50,
    });
  }
}
