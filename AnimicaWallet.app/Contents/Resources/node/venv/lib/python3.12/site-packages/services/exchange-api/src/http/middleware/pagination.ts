/**
 * Pagination Middleware and Utilities
 * Provides cursor-based pagination support
 */

import { z } from 'zod';
import type { Config } from '../../config.js';

/**
 * Standard pagination query schema
 */
export function createPaginationSchema(config: Config) {
  return z.object({
    limit: z
      .string()
      .optional()
      .transform((val) => (val ? parseInt(val, 10) : config.DEFAULT_PAGE_SIZE))
      .refine((val) => val > 0 && val <= config.MAX_PAGE_SIZE, {
        message: `Limit must be between 1 and ${config.MAX_PAGE_SIZE}`,
      }),
    cursor: z.string().optional(),
  });
}

/**
 * Pagination response metadata
 */
export interface PaginationMeta {
  limit: number;
  next_cursor?: string;
  prev_cursor?: string;
  has_more: boolean;
}

/**
 * Paginated response wrapper
 */
export interface PaginatedResponse<T> {
  data: T[];
  pagination: PaginationMeta;
}

/**
 * Create pagination response
 */
export function createPaginationResponse<T>(
  data: T[],
  limit: number,
  cursorFn: (item: T) => string
): PaginatedResponse<T> {
  const hasMore = data.length > limit;
  const items = hasMore ? data.slice(0, limit) : data;

  const nextCursor = hasMore ? cursorFn(items[items.length - 1]) : undefined;

  return {
    data: items,
    pagination: {
      limit,
      next_cursor: nextCursor,
      has_more: hasMore,
    },
  };
}

/**
 * Encode cursor (base64 encoded JSON)
 */
export function encodeCursor(data: Record<string, unknown>): string {
  return Buffer.from(JSON.stringify(data)).toString('base64url');
}

/**
 * Decode cursor
 */
export function decodeCursor(cursor: string): Record<string, unknown> {
  try {
    return JSON.parse(Buffer.from(cursor, 'base64url').toString());
  } catch {
    throw new Error('Invalid cursor format');
  }
}
