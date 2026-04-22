import type { RequestHandler } from 'express';
import type { PlatformService } from '../services/platformService.js';

function parseBearerToken(authHeader: string | undefined): string | null {
  if (!authHeader) return null;
  const [kind, value] = authHeader.split(' ');
  if (!kind || !value) return null;
  if (kind.toLowerCase() !== 'bearer') return null;
  return value.trim();
}

export function sessionAuth(service: PlatformService): RequestHandler {
  return (req, res, next) => {
    const sessionToken = req.header('x-session-token') ?? parseBearerToken(req.header('authorization'));
    if (!sessionToken) {
      res.status(401).json({ error: { message: 'Missing session token' } });
      return;
    }

    try {
      const user = service.authenticateSession(sessionToken);
      req.ctx.user = user;
      next();
    } catch (error) {
      res.status(401).json({ error: { message: (error as Error).message } });
    }
  };
}

export function apiKeyAuth(service: PlatformService, scope: Parameters<PlatformService['authorizeApiKey']>[1]): RequestHandler {
  return (req, res, next) => {
    const authHeader = req.header('authorization');
    const apiKey = parseBearerToken(authHeader);
    if (!apiKey) {
      res.status(401).json({ error: { message: 'Missing API key' } });
      return;
    }

    try {
      const authorized = service.authorizeApiKey(apiKey, scope);
      req.ctx.apiKey = authorized.key;
      req.ctx.project = authorized.project;
      next();
    } catch (error) {
      res.status(401).json({ error: { message: (error as Error).message } });
    }
  };
}

export function providerDaemonAuth(service: PlatformService): RequestHandler {
  return (req, res, next) => {
    const providerId = req.header('x-provider-id');
    const daemonToken = req.header('x-provider-token');
    if (!providerId || !daemonToken) {
      res.status(401).json({ error: { message: 'Missing provider daemon credentials' } });
      return;
    }

    try {
      const provider = service.authenticateProviderDaemon(providerId, daemonToken);
      req.ctx.provider = provider;
      next();
    } catch (error) {
      res.status(401).json({ error: { message: (error as Error).message } });
    }
  };
}

export function internalSecretAuth(secret: string): RequestHandler {
  return (req, res, next) => {
    const value = req.header('x-internal-secret');
    if (!value || value !== secret) {
      res.status(401).json({ error: { message: 'Missing or invalid internal secret' } });
      return;
    }
    next();
  };
}

export const requireAdmin: RequestHandler = (req, res, next) => {
  if (!req.ctx.user || req.ctx.user.role !== 'admin') {
    res.status(403).json({ error: { message: 'Admin role required' } });
    return;
  }
  next();
};
