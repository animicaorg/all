import type { RequestHandler } from 'express';

export const requestContextMiddleware: RequestHandler = (req, _res, next) => {
  req.ctx = {};
  next();
};
