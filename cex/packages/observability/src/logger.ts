/**
 * Structured Logger Configuration
 * Using Pino for high-performance structured logging
 */

import pino from 'pino';
import { redactObject, redactHeaders } from '@cex/security/secrets';

export interface LoggerConfig {
  /**
   * Service name
   */
  service: string;

  /**
   * Environment (dev, staging, production)
   */
  environment: string;

  /**
   * Log level
   */
  level?: string;

  /**
   * Pretty print (for development)
   */
  prettyPrint?: boolean;

  /**
   * Enable redaction
   */
  redact?: boolean;
}

/**
 * Create a configured logger instance
 */
export function createLogger(config: LoggerConfig): pino.Logger {
  const pinoConfig: pino.LoggerOptions = {
    name: config.service,
    level: config.level || (config.environment === 'production' ? 'info' : 'debug'),
    
    // Base fields included in every log
    base: {
      service: config.service,
      env: config.environment,
      pid: process.pid,
      hostname: process.env.HOSTNAME || 'unknown',
    },

    // Timestamp in ISO format
    timestamp: () => `,"ts":"${new Date().toISOString()}"`,

    // Serializers for common objects
    serializers: {
      req: pino.stdSerializers.req,
      res: pino.stdSerializers.res,
      err: pino.stdSerializers.err,
    },

    // Formatters
    formatters: {
      level: (label) => {
        return { level: label };
      },
    },
  };

  // Add pretty printing for development
  if (config.prettyPrint && config.environment !== 'production') {
    return pino({
      ...pinoConfig,
      transport: {
        target: 'pino-pretty',
        options: {
          colorize: true,
          translateTime: 'HH:MM:ss Z',
          ignore: 'pid,hostname',
        },
      },
    });
  }

  const logger = pino(pinoConfig);

  // Wrap logger methods to add redaction if enabled
  if (config.redact) {
    return createRedactingLogger(logger);
  }

  return logger;
}

/**
 * Create a child logger with additional context
 */
export function createChildLogger(
  parent: pino.Logger,
  context: Record<string, any>
): pino.Logger {
  return parent.child(context);
}

/**
 * Wrap logger to automatically redact sensitive data
 */
function createRedactingLogger(logger: pino.Logger): pino.Logger {
  const proxy = new Proxy(logger, {
    get(target, prop) {
      // Wrap logging methods
      if (['trace', 'debug', 'info', 'warn', 'error', 'fatal'].includes(prop as string)) {
        return function (this: any, ...args: any[]) {
          // Redact objects in log arguments
          const redactedArgs = args.map((arg) => {
            if (typeof arg === 'object' && arg !== null) {
              // Special handling for common structures
              if (arg.req && arg.req.headers) {
                arg.req.headers = redactHeaders(arg.req.headers);
              }
              return redactObject(arg);
            }
            return arg;
          });

          return (target[prop as keyof pino.Logger] as Function).apply(target, redactedArgs);
        };
      }

      return target[prop as keyof pino.Logger];
    },
  });

  return proxy as pino.Logger;
}

/**
 * Request logger middleware factory
 * Adds request_id and logs HTTP requests
 */
export function createRequestLogger(logger: pino.Logger) {
  return (req: any, res: any, next: any) => {
    const startTime = Date.now();
    
    // Generate or extract request ID
    req.id = req.id || req.headers['x-request-id'] || generateRequestId();
    
    // Add request ID to response headers
    res.setHeader('X-Request-ID', req.id);

    // Create child logger with request context
    req.log = logger.child({
      request_id: req.id,
      trace_id: req.headers['x-trace-id'],
      span_id: req.headers['x-span-id'],
    });

    // Log request
    req.log.info({
      method: req.method,
      url: req.url,
      ip: req.ip || req.socket.remoteAddress,
      user_agent: req.headers['user-agent'],
    }, 'Incoming request');

    // Log response
    res.on('finish', () => {
      const duration = Date.now() - startTime;
      req.log.info({
        method: req.method,
        url: req.url,
        status: res.statusCode,
        latency_ms: duration,
      }, 'Request completed');
    });

    next();
  };
}

/**
 * Generate a unique request ID
 */
function generateRequestId(): string {
  return `req_${Date.now()}_${Math.random().toString(36).substring(2, 15)}`;
}

/**
 * Audit logger helper
 * Logs to a separate audit stream
 */
export function createAuditLogger(baseLogger: pino.Logger): pino.Logger {
  return baseLogger.child({ audit: true });
}

export type Logger = pino.Logger;
