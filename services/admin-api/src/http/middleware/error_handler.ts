/**
 * Error Handler Middleware
 * Centralized error handling with proper logging
 */

import type { Request, Response, NextFunction } from 'express';
import type { Logger } from '../../utils/logger.js';
import { Prisma } from '@prisma/client';
import { ZodError } from 'zod';

export interface ApiError {
  error: string;
  message: string;
  details?: any;
  requestId?: string;
}

/**
 * Custom application error
 */
export class AppError extends Error {
  constructor(
    public statusCode: number,
    public error: string,
    message: string,
    public details?: any
  ) {
    super(message);
    this.name = 'AppError';
    Error.captureStackTrace(this, this.constructor);
  }
}

/**
 * Create error handler middleware
 */
export function createErrorHandler(logger: Logger) {
  return (err: Error, req: Request, res: Response, next: NextFunction): void => {
    // Don't log if headers already sent
    if (res.headersSent) {
      return next(err);
    }

    const requestId = req.id;

    // Handle custom app errors
    if (err instanceof AppError) {
      logger.warn(
        {
          requestId,
          error: err.error,
          message: err.message,
          details: err.details,
          statusCode: err.statusCode,
        },
        'Application error'
      );

      res.status(err.statusCode).json({
        error: err.error,
        message: err.message,
        details: err.details,
        requestId,
      });
      return;
    }

    // Handle Zod validation errors
    if (err instanceof ZodError) {
      logger.warn(
        {
          requestId,
          issues: err.issues,
        },
        'Validation error'
      );

      res.status(400).json({
        error: 'ValidationError',
        message: 'Invalid request data',
        details: err.issues,
        requestId,
      });
      return;
    }

    // Handle Prisma errors
    if (err instanceof Prisma.PrismaClientKnownRequestError) {
      logger.warn(
        {
          requestId,
          code: err.code,
          meta: err.meta,
        },
        'Database error'
      );

      let message = 'Database operation failed';
      let statusCode = 500;

      if (err.code === 'P2002') {
        message = 'Resource already exists';
        statusCode = 409;
      } else if (err.code === 'P2025') {
        message = 'Resource not found';
        statusCode = 404;
      } else if (err.code === 'P2003') {
        message = 'Referenced resource not found';
        statusCode = 400;
      }

      res.status(statusCode).json({
        error: 'DatabaseError',
        message,
        requestId,
      });
      return;
    }

    // Handle unknown errors
    logger.error(
      {
        requestId,
        error: err.message,
        stack: err.stack,
      },
      'Unhandled error'
    );

    res.status(500).json({
      error: 'InternalError',
      message: 'An unexpected error occurred',
      requestId,
    });
  };
}

/**
 * Not found handler
 */
export function notFoundHandler(req: Request, res: Response): void {
  res.status(404).json({
    error: 'NotFound',
    message: `Route ${req.method} ${req.path} not found`,
    requestId: req.id,
  });
}
