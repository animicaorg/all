import type { NextFunction, Response } from 'express';
import type { RequestWithContext } from '../http/types.js';

const seen = new Map<string, string>();

export function idempotencyMiddleware(req: RequestWithContext, _res: Response, next: NextFunction): void {
  if (!['POST', 'PUT', 'PATCH'].includes(req.method.toUpperCase())) {
    next();
    return;
  }

  const key = req.idempotencyKey;
  if (!key) {
    next();
    return;
  }

  const cacheKey = `${req.method}:${req.path}:${key}`;
  if (seen.has(cacheKey)) {
    req.setTimeout(0);
  } else {
    seen.set(cacheKey, new Date().toISOString());
  }

  next();
}
