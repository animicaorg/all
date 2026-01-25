/**
 * RPC Retry Logic with Exponential Backoff
 */

import type { Logger } from "pino";
import { isRetryableError } from "./errors.js";

interface RetryOptions {
  maxRetries: number;
  baseDelay: number; // base delay in ms
  maxDelay?: number; // max delay in ms
  jitter?: boolean; // add jitter to prevent thundering herd
}

/**
 * Sleep for a given duration
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Calculate exponential backoff delay with optional jitter
 */
function calculateDelay(attempt: number, baseDelay: number, maxDelay: number, jitter: boolean): number {
  const exponentialDelay = baseDelay * Math.pow(2, attempt);
  const delay = Math.min(exponentialDelay, maxDelay);
  
  if (jitter) {
    // Add ±25% jitter
    const jitterAmount = delay * 0.25;
    return delay + (Math.random() * jitterAmount * 2 - jitterAmount);
  }
  
  return delay;
}

/**
 * Retry a function with exponential backoff
 */
export async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  options: RetryOptions,
  logger: Logger,
  context: string
): Promise<T> {
  const { maxRetries, baseDelay, maxDelay = 30000, jitter = true } = options;
  
  let lastError: Error | undefined;
  
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error as Error;
      
      // Check if error is retryable
      if (!isRetryableError(lastError)) {
        logger.warn(
          { error: lastError, context, attempt },
          "Non-retryable error, failing immediately"
        );
        throw lastError;
      }
      
      // If this was the last attempt, throw
      if (attempt >= maxRetries) {
        logger.error(
          { error: lastError, context, attempt },
          "Max retries exceeded"
        );
        throw lastError;
      }
      
      // Calculate delay and wait
      const delay = calculateDelay(attempt, baseDelay, maxDelay, jitter);
      logger.warn(
        { error: lastError, context, attempt, delay },
        "Retryable error, waiting before retry"
      );
      
      await sleep(delay);
    }
  }
  
  // Should never reach here, but TypeScript needs this
  throw lastError || new Error("Unknown retry error");
}
