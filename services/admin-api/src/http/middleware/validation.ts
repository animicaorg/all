/**
 * Validation Middleware
 * Request validation using Zod schemas
 */

import type { Request, Response, NextFunction } from 'express';
import { z, ZodSchema } from 'zod';

/**
 * Validate request body
 */
export function validateBody<T extends ZodSchema>(schema: T) {
  return (req: Request, res: Response, next: NextFunction): void => {
    try {
      req.body = schema.parse(req.body);
      next();
    } catch (error) {
      next(error);
    }
  };
}

/**
 * Validate query parameters
 */
export function validateQuery<T extends ZodSchema>(schema: T) {
  return (req: Request, res: Response, next: NextFunction): void => {
    try {
      req.query = schema.parse(req.query);
      next();
    } catch (error) {
      next(error);
    }
  };
}

/**
 * Validate route parameters
 */
export function validateParams<T extends ZodSchema>(schema: T) {
  return (req: Request, res: Response, next: NextFunction): void => {
    try {
      req.params = schema.parse(req.params);
      next();
    } catch (error) {
      next(error);
    }
  };
}

/**
 * Common validation schemas
 */
export const commonSchemas = {
  uuid: z.string().uuid(),
  paginationQuery: z.object({
    page: z.coerce.number().int().min(1).default(1),
    limit: z.coerce.number().int().min(1).max(100).default(50),
    cursor: z.string().optional(),
  }),
  dateRangeQuery: z.object({
    from: z.coerce.date().optional(),
    to: z.coerce.date().optional(),
  }),
};
