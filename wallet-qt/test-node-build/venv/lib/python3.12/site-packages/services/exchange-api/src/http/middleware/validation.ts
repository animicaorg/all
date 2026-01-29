/**
 * Validation Middleware
 * Uses Zod for request validation
 */

import type { Request, Response, NextFunction } from 'express';
import { z, type ZodSchema } from 'zod';
import { ValidationError } from '../../utils/errors.js';

export interface ValidatedRequest<
  TBody = unknown,
  TQuery = unknown,
  TParams = unknown
> extends Request {
  validated: {
    body: TBody;
    query: TQuery;
    params: TParams;
  };
}

export function validate<
  TBody extends ZodSchema = ZodSchema<unknown>,
  TQuery extends ZodSchema = ZodSchema<unknown>,
  TParams extends ZodSchema = ZodSchema<unknown>
>(schemas: {
  body?: TBody;
  query?: TQuery;
  params?: TParams;
}) {
  return (req: Request, _res: Response, next: NextFunction) => {
    try {
      const validated = {
        body: schemas.body ? schemas.body.parse(req.body) : req.body,
        query: schemas.query ? schemas.query.parse(req.query) : req.query,
        params: schemas.params ? schemas.params.parse(req.params) : req.params,
      };

      (req as ValidatedRequest).validated = validated;
      next();
    } catch (error) {
      if (error instanceof z.ZodError) {
        throw new ValidationError('Validation failed', {
          errors: error.errors.map((e) => ({
            path: e.path.join('.'),
            message: e.message,
            code: e.code,
          })),
        });
      }
      throw error;
    }
  };
}
