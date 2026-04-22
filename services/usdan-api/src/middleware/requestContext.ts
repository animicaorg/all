import { randomUUID } from 'node:crypto';
import type { NextFunction, Response } from 'express';
import type { RequestWithContext } from '../http/types.js';

export function requestContextMiddleware(req: RequestWithContext, res: Response, next: NextFunction): void {
  const requestId = req.header('x-request-id') ?? randomUUID();
  req.requestId = requestId;
  res.setHeader('x-request-id', requestId);

  const idempotencyKey = req.header('idempotency-key') ?? undefined;
  req.idempotencyKey = idempotencyKey;

  next();
}
