/**
 * Animica Explorer — Global Error Handler
 * -----------------------------------------------------------------------------
 * Handles unhandled promise rejections and global errors.
 * Integrates with the toast system to provide user feedback.
 */

type ErrorKind = 'network' | 'rpc' | 'parse' | 'timeout' | 'unknown';

interface ErrorContext {
  kind: ErrorKind;
  message: string;
  originalError: any;
  url?: string;
  method?: string;
  timestamp: number;
}

/**
 * Categorize an error and extract useful information.
 */
export function categorizeError(error: any): ErrorContext {
  const timestamp = Date.now();
  const context: ErrorContext = {
    kind: 'unknown',
    message: 'An unexpected error occurred',
    originalError: error,
    timestamp,
  };

  // Extract error message
  if (error instanceof Error) {
    context.message = error.message;
  } else if (typeof error === 'string') {
    context.message = error;
  } else if (error?.message) {
    context.message = String(error.message);
  }

  // Categorize by error type or message
  const msg = context.message.toLowerCase();

  if (
    error?.name === 'NetworkError' ||
    (error?.name === 'TypeError' && msg.includes('fetch')) ||
    msg.includes('network') ||
    msg.includes('failed to fetch') ||
    msg.includes('load failed')
  ) {
    context.kind = 'network';
    context.message = 'Network error: Unable to reach the RPC server. Please check your connection.';
  } else if (
    error?.name === 'TimeoutError' ||
    msg.includes('timeout') ||
    msg.includes('timed out')
  ) {
    context.kind = 'timeout';
    context.message = 'Request timed out. The RPC server may be slow or unresponsive.';
  } else if (
    error?.name === 'RpcError' ||
    error?.name === 'HttpError' ||
    msg.includes('rpc') ||
    msg.includes('json-rpc')
  ) {
    context.kind = 'rpc';
    // Keep original message for RPC errors as they're usually informative
  } else if (
    error?.name === 'ParseError' ||
    error?.name === 'SyntaxError' ||
    msg.includes('parse') ||
    msg.includes('json')
  ) {
    context.kind = 'parse';
    context.message = 'Failed to parse server response. The RPC server may be misconfigured.';
  }

  // Extract URL if available
  if (error?.url) {
    context.url = error.url;
  }

  // Extract method if available
  if (error?.method) {
    context.method = error.method;
  }

  return context;
}

/**
 * Get user-friendly error message with actionable advice.
 */
export function getUserFriendlyMessage(context: ErrorContext): string {
  const baseMessage = context.message;
  const troubleshooting: string[] = [];

  switch (context.kind) {
    case 'network':
      troubleshooting.push(
        'Check that the RPC server is running and accessible',
        'Verify your internet connection',
        'Ensure CORS is enabled on the RPC server',
        'Check the browser console (F12) for more details'
      );
      break;

    case 'timeout':
      troubleshooting.push(
        'The RPC server may be experiencing high load',
        'Try refreshing the page',
        'Check your network latency'
      );
      break;

    case 'rpc':
      troubleshooting.push(
        'The RPC server returned an error',
        'Check the chain ID configuration',
        'Verify the RPC endpoint URL',
        'Review the browser console for details'
      );
      break;

    case 'parse':
      troubleshooting.push(
        'The server response was malformed',
        'Check if the RPC endpoint is correct',
        'The server may be misconfigured or down'
      );
      break;

    default:
      troubleshooting.push(
        'Try refreshing the page',
        'Check the browser console (F12) for details',
        'Clear browser cache if the issue persists'
      );
  }

  let fullMessage = baseMessage;
  if (context.url) {
    fullMessage += `\n\nEndpoint: ${context.url}`;
  }
  if (context.method) {
    fullMessage += `\nMethod: ${context.method}`;
  }
  if (troubleshooting.length > 0) {
    fullMessage += '\n\n💡 Troubleshooting:\n• ' + troubleshooting.join('\n• ');
  }

  return fullMessage;
}

/**
 * Show an error toast with categorized error information.
 */
export function showErrorToast(error: any, title?: string) {
  const context = categorizeError(error);
  const message = getUserFriendlyMessage(context);

  try {
    window.dispatchEvent(
      new CustomEvent('explorer:toast', {
        detail: {
          kind: 'error',
          title: title || 'Error',
          message,
          durationMs: 10000, // Longer duration for error messages
        },
      })
    );
  } catch (e) {
    // Fallback to console if toast system fails
    console.error('[errorHandler] Failed to show toast:', e);
    console.error('[errorHandler] Original error:', error);
  }
}

/**
 * Install global error handlers.
 * Call this once during app initialization.
 */
export function installGlobalErrorHandlers() {
  // Only install once (even across StrictMode double-invocation)
  const flag = '__animica_explorer_error_handlers__';
  if (typeof window !== 'undefined' && (window as any)[flag]) {
    console.log('[errorHandler] Global error handlers already installed');
    return;
  }

  if (typeof window !== 'undefined') {
    (window as any)[flag] = true;
  }

  // Handle unhandled promise rejections
  unhandledRejectionHandler = (event) => {
    console.error('[errorHandler] Unhandled promise rejection:', event.reason);
    
    // Prevent default browser behavior (console warning)
    event.preventDefault();
    
    showErrorToast(event.reason, 'Unhandled Error');
  };
  window.addEventListener('unhandledrejection', unhandledRejectionHandler);

  // Handle global errors
  globalErrorHandler = (event) => {
    console.error('[errorHandler] Global error:', event.error || event.message);
    
    // Don't show toast for script loading errors (they're usually handled elsewhere)
    if (event.filename && !event.error) {
      return;
    }
    
    showErrorToast(event.error || event.message, 'Application Error');
  };
  window.addEventListener('error', globalErrorHandler);

  console.log('[errorHandler] Global error handlers installed');
}

// Store handler references for cleanup
let unhandledRejectionHandler: ((event: PromiseRejectionEvent) => void) | null = null;
let globalErrorHandler: ((event: ErrorEvent) => void) | null = null;

/**
 * Remove global error handlers (for cleanup in tests).
 */
export function uninstallGlobalErrorHandlers() {
  if (unhandledRejectionHandler) {
    window.removeEventListener('unhandledrejection', unhandledRejectionHandler);
    unhandledRejectionHandler = null;
  }
  if (globalErrorHandler) {
    window.removeEventListener('error', globalErrorHandler);
    globalErrorHandler = null;
  }
  console.log('[errorHandler] Global error handlers uninstalled');
}

/**
 * Wrap an async function with error handling.
 * Useful for event handlers and callbacks.
 */
export function withErrorHandling<T extends (...args: any[]) => Promise<any>>(
  fn: T,
  errorTitle?: string
): T {
  return (async (...args: any[]) => {
    try {
      return await fn(...args);
    } catch (error) {
      console.error('[withErrorHandling] Caught error:', error);
      showErrorToast(error, errorTitle);
      throw error; // Re-throw to allow caller to handle if needed
    }
  }) as T;
}

/**
 * Wrap a promise to catch and log errors without throwing.
 * Returns null on error.
 */
export async function safeAsync<T>(
  promise: Promise<T>,
  errorTitle?: string
): Promise<T | null> {
  try {
    return await promise;
  } catch (error) {
    console.error('[safeAsync] Caught error:', error);
    showErrorToast(error, errorTitle);
    return null;
  }
}
