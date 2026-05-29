import express from 'express';
import cors from 'cors';
import cookieParser from 'cookie-parser';
import { corsOrigins, env, isProd } from './env';
import { attachUser } from './middleware/auth';
import { authRouter } from './routes/auth';
import { healthRouter } from './routes/health';
import { conversationsRouter } from './routes/conversations';
import { chatRouter } from './routes/chat';
import { billingRouter } from './routes/billing';
import { paypalRouter, paypalWebhookRouter } from './routes/paypal';
import { adminRouter } from './routes/admin';
import { integrationsRouter } from './routes/integrations';
import { aicfTiersRouter } from './routes/aicfTiers';

const app = express();

// Trust the first proxy hop so req.ip reflects the real client behind nginx.
app.set('trust proxy', 1);

// Webhooks need raw bodies for signature verification — mount BEFORE
// the global express.json().
app.use('/api/webhooks/paypal', paypalWebhookRouter);

app.use(express.json({ limit: '1mb' }));
app.use(cookieParser());
app.use(
  cors({
    origin: corsOrigins,
    credentials: true,
  }),
);
app.use(attachUser);

app.use('/api/health', healthRouter);
app.use('/api/auth', authRouter);
app.use('/api/conversations', conversationsRouter);
app.use('/api/chat', chatRouter);
app.use('/api/billing', billingRouter);
app.use('/api/paypal', paypalRouter);
app.use('/api/admin', adminRouter);
app.use('/api/integrations', integrationsRouter);
app.use('/api/aicf/tiers', aicfTiersRouter);

app.use((_req, res) => {
  res.status(404).json({ error: 'not_found' });
});

// Centralised error handler. Keep response bodies generic in prod so
// stack traces don't leak; full errors still go to the log.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  // eslint-disable-next-line no-console
  console.error('[unhandled]', err);
  const status = (err as { status?: number }).status ?? 500;
  if (isProd) {
    res.status(status).json({ error: 'internal_error' });
    return;
  }
  res.status(status).json({
    error: 'internal_error',
    message: err instanceof Error ? err.message : String(err),
  });
});

const port = env.PORT;
app.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`[animica-chat-server] listening on ${port} (${env.NODE_ENV})`);
});
