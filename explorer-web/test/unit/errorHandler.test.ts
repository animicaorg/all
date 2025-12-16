import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  categorizeError,
  getUserFriendlyMessage,
  showErrorToast,
  installGlobalErrorHandlers,
  withErrorHandling,
  safeAsync,
} from '../../src/utils/errorHandler';

describe('errorHandler', () => {
  let dispatchEventSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    dispatchEventSpy = vi.spyOn(window, 'dispatchEvent');
  });

  afterEach(() => {
    dispatchEventSpy.mockRestore();
  });

  describe('categorizeError', () => {
    it('categorizes network errors', () => {
      const error = new Error('Failed to fetch');
      error.name = 'NetworkError';
      const context = categorizeError(error);

      expect(context.kind).toBe('network');
      expect(context.message).toContain('Network error');
    });

    it('categorizes timeout errors', () => {
      const error = new Error('Request timed out after 5000ms');
      error.name = 'TimeoutError';
      const context = categorizeError(error);

      expect(context.kind).toBe('timeout');
      expect(context.message).toContain('timed out');
    });

    it('categorizes RPC errors', () => {
      const error = new Error('JSON-RPC error -32603');
      error.name = 'RpcError';
      const context = categorizeError(error);

      expect(context.kind).toBe('rpc');
    });

    it('categorizes parse errors', () => {
      const error = new Error('Invalid JSON');
      error.name = 'SyntaxError';
      const context = categorizeError(error);

      expect(context.kind).toBe('parse');
      expect(context.message).toContain('parse');
    });

    it('handles unknown error types', () => {
      const error = new Error('Something went wrong');
      const context = categorizeError(error);

      expect(context.kind).toBe('unknown');
      expect(context.message).toBeDefined();
    });

    it('extracts error message from string', () => {
      const context = categorizeError('Simple error string');

      expect(context.message).toBe('Simple error string');
    });

    it('extracts error message from object with message', () => {
      const error = { message: 'Custom error object' };
      const context = categorizeError(error);

      expect(context.message).toBe('Custom error object');
    });
  });

  describe('getUserFriendlyMessage', () => {
    it('provides troubleshooting for network errors', () => {
      const context = {
        kind: 'network' as const,
        message: 'Network error',
        originalError: new Error('Network error'),
        timestamp: Date.now(),
      };

      const message = getUserFriendlyMessage(context);

      expect(message).toContain('Network error');
      expect(message).toContain('Troubleshooting');
      expect(message).toContain('RPC server');
    });

    it('includes URL when available', () => {
      const context = {
        kind: 'network' as const,
        message: 'Network error',
        originalError: new Error('Network error'),
        url: 'http://localhost:8545',
        timestamp: Date.now(),
      };

      const message = getUserFriendlyMessage(context);

      expect(message).toContain('http://localhost:8545');
    });

    it('includes method when available', () => {
      const context = {
        kind: 'rpc' as const,
        message: 'RPC error',
        originalError: new Error('RPC error'),
        method: 'chain.getChainId',
        timestamp: Date.now(),
      };

      const message = getUserFriendlyMessage(context);

      expect(message).toContain('chain.getChainId');
    });
  });

  describe('showErrorToast', () => {
    it('dispatches a toast event', () => {
      const error = new Error('Test error');
      showErrorToast(error, 'Test Title');

      expect(dispatchEventSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'explorer:toast',
          detail: expect.objectContaining({
            kind: 'error',
            title: 'Test Title',
          }),
        })
      );
    });

    it('uses default title when not provided', () => {
      const error = new Error('Test error');
      showErrorToast(error);

      expect(dispatchEventSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'explorer:toast',
          detail: expect.objectContaining({
            kind: 'error',
            title: 'Error',
          }),
        })
      );
    });

    it('handles errors during toast dispatch gracefully', () => {
      dispatchEventSpy.mockImplementation(() => {
        throw new Error('Toast dispatch failed');
      });

      // Should not throw
      expect(() => showErrorToast(new Error('Test'))).not.toThrow();
    });
  });

  describe('installGlobalErrorHandlers', () => {
    it('installs handlers without errors', () => {
      expect(() => installGlobalErrorHandlers()).not.toThrow();
    });

    it('handles unhandled promise rejections', async () => {
      installGlobalErrorHandlers();

      const errorEvent = new PromiseRejectionEvent('unhandledrejection', {
        promise: Promise.reject('Test rejection'),
        reason: 'Test rejection',
      });

      window.dispatchEvent(errorEvent);

      // Wait a tick for async handling
      await new Promise((resolve) => setTimeout(resolve, 10));

      expect(dispatchEventSpy).toHaveBeenCalled();
    });
  });

  describe('withErrorHandling', () => {
    it('wraps async function and handles errors', async () => {
      const fn = vi.fn().mockRejectedValue(new Error('Test error'));
      const wrapped = withErrorHandling(fn, 'Test Function');

      await expect(wrapped()).rejects.toThrow('Test error');
      expect(dispatchEventSpy).toHaveBeenCalled();
    });

    it('passes through successful results', async () => {
      const fn = vi.fn().mockResolvedValue('success');
      const wrapped = withErrorHandling(fn);

      const result = await wrapped();

      expect(result).toBe('success');
      expect(dispatchEventSpy).not.toHaveBeenCalled();
    });

    it('forwards arguments to wrapped function', async () => {
      const fn = vi.fn().mockResolvedValue('success');
      const wrapped = withErrorHandling(fn);

      await wrapped('arg1', 'arg2', 123);

      expect(fn).toHaveBeenCalledWith('arg1', 'arg2', 123);
    });
  });

  describe('safeAsync', () => {
    it('returns result on success', async () => {
      const promise = Promise.resolve('success');
      const result = await safeAsync(promise);

      expect(result).toBe('success');
      expect(dispatchEventSpy).not.toHaveBeenCalled();
    });

    it('returns null on error and shows toast', async () => {
      const promise = Promise.reject(new Error('Test error'));
      const result = await safeAsync(promise, 'Safe Operation');

      expect(result).toBeNull();
      expect(dispatchEventSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'explorer:toast',
          detail: expect.objectContaining({
            kind: 'error',
            title: 'Safe Operation',
          }),
        })
      );
    });

    it('uses default title when not provided', async () => {
      const promise = Promise.reject(new Error('Test error'));
      const result = await safeAsync(promise);

      expect(result).toBeNull();
      expect(dispatchEventSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'explorer:toast',
          detail: expect.objectContaining({
            kind: 'error',
          }),
        })
      );
    });
  });
});
