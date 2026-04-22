import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import pinoHttp from 'pino-http';
import type { HttpContext } from './context.js';
import { createRouters } from './routes/index.js';
import { createAdminAuthMiddleware, createUserAuthMiddleware } from '../middleware/auth.js';
import { createRateLimiter } from '../middleware/rateLimit.js';
import { requestContextMiddleware } from '../middleware/requestContext.js';
import { idempotencyMiddleware } from '../middleware/idempotency.js';
import { createErrorHandler } from '../middleware/errorHandler.js';
import type { RequestWithContext } from './types.js';

export function createApp(ctx: HttpContext): express.Express {
  const app = express();
  const userAuth = createUserAuthMiddleware(ctx.config);
  const adminAuth = createAdminAuthMiddleware(ctx.config);
  const rateLimiter = createRateLimiter(ctx.config);
  const pinoHttpFactory = pinoHttp as unknown as (options: Record<string, unknown>) => express.RequestHandler;

  app.use(helmet());
  app.use(cors());
  app.use(requestContextMiddleware);
  app.use(
    pinoHttpFactory({
      logger: ctx.logger,
      customSuccessMessage: () => 'request completed'
    })
  );
  app.use(rateLimiter as unknown as express.RequestHandler);

  app.use(
    express.json({
      limit: '1mb',
      verify: (req, _res, buf) => {
        (req as RequestWithContext as any).rawBody = buf.toString('utf8');
      }
    })
  );
  app.use(idempotencyMiddleware);

  const routes = createRouters(ctx);

  app.use(routes.health);
  app.use(routes.auth);
  app.use(routes.webhooks);

  app.use(userAuth, routes.wallet);
  app.use(userAuth, routes.kyc);
  app.use(userAuth, routes.buy);
  app.use(userAuth, routes.redeem);
  app.use(userAuth, routes.transactions);
  app.use(userAuth, routes.support);

  app.use(routes.reserves);
  app.use(adminAuth, routes.admin);

  app.use(createErrorHandler(ctx.logger));

  return app;
}
