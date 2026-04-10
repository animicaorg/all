/**
 * Error Handler Middleware
 * Converts errors to standard JSON responses
 */

import type { Request, Response, NextFunction } from 'express';
import type { Logger } from '../../utils/logger.js';
import { ApiError, InternalServerError } from '../../utils/errors.js';
import type { RequestWithId } from './request_id.js';

export function createErrorHandler(logger: Logger) {
  return (
    err: Error,
    req: Request,
    res: Response,
    _next: NextFunction
  ) => {
    const requestId = (req as RequestWithId).id;

    if (err instanceof ApiError) {
      // Known API errors
      logger.warn(
        {
          error: err,
          code: err.code,
          statusCode: err.statusCode,
          requestId,
          path: req.path,
          method: req.method,
        },
        'API error'
      );

      return res.status(err.statusCode).json(err.toJSON(requestId));
    }

    // Unknown errors - log and return generic error
    logger.error(
      {
        error: err,
        stack: err.stack,
        requestId,
        path: req.path,
        method: req.method,
      },
      'Unhandled error'
    );

    const internalError = new InternalServerError();
    return res.status(500).json(internalError.toJSON(requestId));
  };
}
