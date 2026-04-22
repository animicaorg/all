import type { NextFunction, Request, Response } from 'express';
import type { Logger } from '../logger.js';
import { ApiError } from '../lib/errors.js';

export function createErrorHandler(logger: Logger) {
  return (err: unknown, req: Request, res: Response, _next: NextFunction): void => {
    if (err instanceof ApiError) {
      res.status(err.statusCode).json({
        error: {
          code: err.code,
          message: err.message,
          details: err.details,
          requestId: res.getHeader('x-request-id')
        }
      });
      return;
    }

    logger.error({ err, path: req.path }, 'Unhandled API error');
    res.status(500).json({
      error: {
        code: 'INTERNAL_ERROR',
        message: 'Internal server error',
        requestId: res.getHeader('x-request-id')
      }
    });
  };
}
