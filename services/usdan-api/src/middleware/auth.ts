import type { NextFunction, Response } from 'express';
import jwt from 'jsonwebtoken';
import type { Config } from '../config.js';
import { ApiError } from '../lib/errors.js';
import type { RequestWithContext } from '../http/types.js';

export function createUserAuthMiddleware(config: Config) {
  return (req: RequestWithContext, _res: Response, next: NextFunction): void => {
    try {
      const auth = req.header('authorization');
      if (!auth || !auth.startsWith('Bearer ')) {
        throw new ApiError(401, 'AUTH_REQUIRED', 'Missing bearer token');
      }
      const token = auth.slice('Bearer '.length).trim();
      const payload = jwt.verify(token, config.USDAN_API_JWT_SECRET) as {
        sub: string;
        walletAddress?: string;
        scope: 'user';
      };
      req.user = {
        userId: payload.sub,
        walletAddress: payload.walletAddress,
        scope: 'user'
      };
      next();
    } catch (error) {
      next(error);
    }
  };
}

export function createAdminAuthMiddleware(config: Config) {
  return (req: RequestWithContext, _res: Response, next: NextFunction): void => {
    try {
      const token = req.header('x-admin-api-key');
      if (!token || token !== config.USDAN_API_ADMIN_API_KEY) {
        throw new ApiError(401, 'ADMIN_AUTH_REQUIRED', 'Missing or invalid admin API key');
      }
      req.admin = {
        actorId: 'admin_api_key',
        scope: 'admin'
      };
      next();
    } catch (error) {
      next(error);
    }
  };
}
