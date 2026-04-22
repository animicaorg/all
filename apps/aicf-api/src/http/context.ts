import type { RequestContext } from './types.js';

declare global {
  namespace Express {
    interface Request {
      ctx: RequestContext;
    }
  }
}

export {};
