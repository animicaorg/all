/**
 * Retry Logic Utilities
 */

import type { Logger } from "pino";

/**
 * Calculate exponential backoff delay
 */
export function calculateBackoff(
  attemptCount: number,
  baseDelayMs: number = 1000,
  maxDelayMs: number = 300000 // 5 minutes
): number {
  const delay = Math.min(
    baseDelayMs * Math.pow(2, attemptCount),
    maxDelayMs
  );
  
  // Add jitter (±10%)
  const jitter = delay * 0.1 * (Math.random() * 2 - 1);
  
  return Math.floor(delay + jitter);
}

/**
 * Execute operation with retries
 */
export async function retryOperation<T>(
  operation: () => Promise<T>,
  maxAttempts: number,
  logger: Logger,
  operationName: string = "operation"
): Promise<{ success: boolean; result?: T; error?: any; attempts: number }> {
  let lastError: any;
  
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      logger.debug(
        { attempt: attempt + 1, maxAttempts, operationName },
        "Attempting operation"
      );
      
      const result = await operation();
      
      logger.info(
        { attempt: attempt + 1, operationName },
        "Operation succeeded"
      );
      
      return {
        success: true,
        result,
        attempts: attempt + 1,
      };
    } catch (error) {
      lastError = error;
      
      logger.warn(
        {
          error,
          attempt: attempt + 1,
          maxAttempts,
          operationName,
        },
        "Operation attempt failed"
      );
      
      // Don't wait after the last attempt
      if (attempt < maxAttempts - 1) {
        const delayMs = calculateBackoff(attempt);
        logger.debug(
          { delayMs, attempt: attempt + 1 },
          "Waiting before retry"
        );
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
    }
  }
  
  logger.error(
    {
      error: lastError,
      maxAttempts,
      operationName,
    },
    "Operation failed after all retries"
  );
  
  return {
    success: false,
    error: lastError,
    attempts: maxAttempts,
  };
}

/**
 * Check if error is retryable
 */
export function isRetryableError(error: any): boolean {
  // Network errors
  if (error.code === "ECONNREFUSED" || error.code === "ETIMEDOUT") {
    return true;
  }
  
  // HTTP 5xx errors
  if (error.response?.status >= 500) {
    return true;
  }
  
  // Rate limit errors
  if (error.response?.status === 429) {
    return true;
  }
  
  // Default: not retryable
  return false;
}
