/**
 * Request ID Middleware
 * Adds a unique request ID to each request for tracing
 */

import type { Request, Response, NextFunction } from 'express';
import { v4 as uuidv4 } from 'uuid';

export interface RequestWithId extends Request {
  id: string;
}

export function requestIdMiddleware(
  req: RequestWithId,
  res: Response,
  next: NextFunction
) {
  req.id = (req.headers['x-request-id'] as string) || uuidv4();
  res.setHeader('X-Request-ID', req.id);
  next();
}
