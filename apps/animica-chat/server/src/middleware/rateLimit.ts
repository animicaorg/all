import rateLimit from 'express-rate-limit';

export const authRateLimit = rateLimit({
  windowMs: 60_000,
  limit: 30,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
});

export const chatRateLimit = rateLimit({
  windowMs: 60_000,
  limit: 60,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
});

export const webhookRateLimit = rateLimit({
  windowMs: 60_000,
  limit: 600,
  standardHeaders: false,
  legacyHeaders: false,
});
