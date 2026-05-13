/**
 * Discriminated union types for background RPC responses
 *
 * All background message handlers should return these types instead of raw values
 * to ensure proper error handling and prevent undefined crashes.
 */

import { stringifySafe } from '../core/rpc/safeJson';

export type Result<T, E = string> =
  | { ok: true; value: T }
  | { ok: false; error: E };

export interface ErrorDetail {
  code: string;
  message: string;
  details?: unknown;
}

/**
 * Standard error response for background RPC
 */
export interface RpcErrorResponse {
  error: string | ErrorDetail;
}

/**
 * Helper to create a success result
 */
export function success<T>(value: T): Result<T, never> {
  return { ok: true, value };
}

/**
 * Helper to create an error result
 */
export function failure<E = string>(error: E): Result<never, E> {
  return { ok: false, error };
}

/**
 * Helper to create an error detail
 */
export function errorDetail(code: string, message: string, details?: unknown): ErrorDetail {
  return { code, message, details };
}

/**
 * Type guard for Result success
 */
export function isSuccess<T, E>(result: Result<T, E>): result is { ok: true; value: T } {
  return result.ok === true;
}

/**
 * Type guard for Result failure
 */
export function isFailure<T, E>(result: Result<T, E>): result is { ok: false; error: E } {
  return result.ok === false;
}

/**
 * Unwrap a Result, throwing if it's an error
 */
export function unwrap<T, E>(result: Result<T, E>): T {
  if (isSuccess(result)) {
    return result.value;
  }
  // result.error can be any payload — RPC errors sometimes carry bigint fields.
  // stringifySafe routes bigints to decimal strings so a real error never gets
  // replaced with `Do not know how to serialize a BigInt`.
  throw new Error(typeof result.error === 'string' ? result.error : stringifySafe(result.error));
}

/**
 * Unwrap a Result, returning a default value if it's an error
 */
export function unwrapOr<T, E>(result: Result<T, E>, defaultValue: T): T {
  return isSuccess(result) ? result.value : defaultValue;
}
