/**
 * HTTP Middleware Exports
 */

export {
  createApiKeyAuthMiddleware,
  requireScopes,
  type ApiKeyAuthRequest,
} from './api_key_auth.js';

export {
  extractSignatureComponents,
  buildPrehashString,
  computeSignature,
  verifySignature,
  hashBody,
  type SignatureComponents,
} from './signature_auth.js';

export { createCorsMiddleware } from './cors.js';
export { createErrorHandler } from './error_handler.js';
export { requestIdMiddleware } from './request_id.js';
export {
  validate,
  type ValidatedRequest,
} from './validation.js';

export {
  createPaginationSchema,
  createPaginationResponse,
  encodeCursor,
  decodeCursor,
  type PaginationMeta,
  type PaginatedResponse,
} from './pagination.js';

export {
  createRateLimiter,
  createRateLimiters,
  createRedisRateLimiter,
  createInMemoryRateLimiter,
  type RateLimitConfig,
  type AuthenticatedRequest,
} from './rate_limit.js';
