/**
 * Users Client
 * 
 * Provides access to user data and profiles using Prisma.
 */

import { PrismaClient, User, UserProfile } from '@prisma/client';
import { Logger } from '../utils/logger.js';

export interface UserWithProfile extends User {
  profile: UserProfile | null;
}

export interface UserFilters {
  email?: string;
  status?: string;
  kycStatus?: string;
}

export interface PaginationOptions {
  skip?: number;
  take?: number;
}

export class UsersClient {
  private prisma: PrismaClient;
  private logger: Logger;

  constructor(prisma: PrismaClient, logger: Logger) {
    this.prisma = prisma;
    this.logger = logger;
  }

  /**
   * Get a user by ID
   */
  async getUser(userId: string): Promise<User | null> {
    return this.prisma.user.findUnique({
      where: { id: userId },
    });
  }

  /**
   * Get a user with their profile
   */
  async getUserProfile(userId: string): Promise<UserWithProfile | null> {
    return this.prisma.user.findUnique({
      where: { id: userId },
      include: {
        profile: true,
      },
    });
  }

  /**
   * Get a user by email
   */
  async getUserByEmail(email: string): Promise<User | null> {
    return this.prisma.user.findUnique({
      where: { email },
    });
  }

  /**
   * Get multiple users with filters and pagination
   */
  async getUsers(
    filters?: UserFilters,
    pagination?: PaginationOptions
  ): Promise<UserWithProfile[]> {
    const where: any = {};

    if (filters?.email) {
      where.email = { contains: filters.email, mode: 'insensitive' };
    }

    if (filters?.status) {
      where.status = filters.status;
    }

    if (filters?.kycStatus) {
      where.kycStatus = filters.kycStatus;
    }

    return this.prisma.user.findMany({
      where,
      include: {
        profile: true,
      },
      skip: pagination?.skip,
      take: pagination?.take,
      orderBy: { createdAt: 'desc' },
    });
  }

  /**
   * Check if a user exists
   */
  async userExists(userId: string): Promise<boolean> {
    const count = await this.prisma.user.count({
      where: { id: userId },
    });
    return count > 0;
  }

  /**
   * Get user count with optional filters
   */
  async getUserCount(filters?: UserFilters): Promise<number> {
    const where: any = {};

    if (filters?.email) {
      where.email = { contains: filters.email, mode: 'insensitive' };
    }

    if (filters?.status) {
      where.status = filters.status;
    }

    if (filters?.kycStatus) {
      where.kycStatus = filters.kycStatus;
    }

    return this.prisma.user.count({ where });
  }
}
