/**
 * Standard Error Response Types
 */

export interface ErrorResponse {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
}

export class ApiError extends Error {
  constructor(
    public code: string,
    public statusCode: number,
    message: string,
    public details?: Record<string, unknown>
  ) {
    super(message);
    this.name = 'ApiError';
  }

  toJSON(requestId?: string): ErrorResponse {
    return {
      error: {
        code: this.code,
        message: this.message,
        details: this.details,
        request_id: requestId,
      },
    };
  }
}

// Common API Errors
export class UnauthorizedError extends ApiError {
  constructor(message = 'Unauthorized', details?: Record<string, unknown>) {
    super('UNAUTHORIZED', 401, message, details);
  }
}

export class ForbiddenError extends ApiError {
  constructor(message = 'Forbidden', details?: Record<string, unknown>) {
    super('FORBIDDEN', 403, message, details);
  }
}

export class NotFoundError extends ApiError {
  constructor(message = 'Not found', details?: Record<string, unknown>) {
    super('NOT_FOUND', 404, message, details);
  }
}

export class ValidationError extends ApiError {
  constructor(message = 'Validation failed', details?: Record<string, unknown>) {
    super('VALIDATION_ERROR', 400, message, details);
  }
}

export class RateLimitError extends ApiError {
  constructor(
    public retryAfterMs: number,
    public limit: number,
    public remaining: number,
    public resetMs: number
  ) {
    super('RATE_LIMITED', 429, 'Rate limit exceeded', {
      retry_after_ms: retryAfterMs,
      limit,
      remaining,
      reset_ms: resetMs,
    });
  }
}

export class InternalServerError extends ApiError {
  constructor(message = 'Internal server error', details?: Record<string, unknown>) {
    super('INTERNAL_ERROR', 500, message, details);
  }
}

export class BadRequestError extends ApiError {
  constructor(message = 'Bad request', details?: Record<string, unknown>) {
    super('BAD_REQUEST', 400, message, details);
  }
}

export class ConflictError extends ApiError {
  constructor(message = 'Conflict', details?: Record<string, unknown>) {
    super('CONFLICT', 409, message, details);
  }
}
