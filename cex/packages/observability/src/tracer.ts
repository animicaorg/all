/**
 * OpenTelemetry Tracing Configuration
 * Provides distributed tracing capabilities
 */

import { NodeSDK } from '@opentelemetry/sdk-node';
import { Resource } from '@opentelemetry/resources';
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from '@opentelemetry/semantic-conventions';
import { HttpInstrumentation } from '@opentelemetry/instrumentation-http';
import { ExpressInstrumentation } from '@opentelemetry/instrumentation-express';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import * as api from '@opentelemetry/api';

export interface TracingConfig {
  /**
   * Service name
   */
  serviceName: string;

  /**
   * Service version
   */
  serviceVersion?: string;

  /**
   * OTLP exporter endpoint (e.g., http://localhost:4318/v1/traces)
   * If not provided, tracing is disabled
   */
  otlpEndpoint?: string;

  /**
   * Enable HTTP instrumentation
   */
  enableHttp?: boolean;

  /**
   * Enable Express instrumentation
   */
  enableExpress?: boolean;

  /**
   * Sample rate (0.0 to 1.0)
   */
  sampleRate?: number;
}

let sdk: NodeSDK | null = null;

/**
 * Initialize OpenTelemetry tracing
 */
export function initializeTracing(config: TracingConfig): void {
  if (!config.otlpEndpoint) {
    console.log('Tracing disabled (no OTLP endpoint configured)');
    return;
  }

  const resource = new Resource({
    [ATTR_SERVICE_NAME]: config.serviceName,
    [ATTR_SERVICE_VERSION]: config.serviceVersion || '0.0.0',
  });

  const instrumentations = [];

  if (config.enableHttp !== false) {
    instrumentations.push(new HttpInstrumentation());
  }

  if (config.enableExpress !== false) {
    instrumentations.push(new ExpressInstrumentation());
  }

  const traceExporter = new OTLPTraceExporter({
    url: config.otlpEndpoint,
  });

  sdk = new NodeSDK({
    resource,
    traceExporter,
    instrumentations,
  });

  sdk.start();
  console.log('OpenTelemetry tracing initialized');
}

/**
 * Shutdown tracing gracefully
 */
export async function shutdownTracing(): Promise<void> {
  if (sdk) {
    await sdk.shutdown();
    sdk = null;
  }
}

/**
 * Get the active tracer
 */
export function getTracer(name: string): api.Tracer {
  return api.trace.getTracer(name);
}

/**
 * Create a new span
 */
export function startSpan(
  tracer: api.Tracer,
  name: string,
  attributes?: Record<string, any>
): api.Span {
  return tracer.startSpan(name, {
    attributes,
  });
}

/**
 * Wrap a function with tracing
 */
export function traced<T extends (...args: any[]) => any>(
  tracer: api.Tracer,
  spanName: string,
  fn: T
): T {
  return ((...args: any[]) => {
    return api.context.with(
      api.trace.setSpan(api.context.active(), tracer.startSpan(spanName)),
      () => {
        const span = api.trace.getSpan(api.context.active());
        try {
          const result = fn(...args);
          
          // Handle promises
          if (result instanceof Promise) {
            return result
              .then((value) => {
                span?.end();
                return value;
              })
              .catch((error) => {
                span?.recordException(error);
                span?.setStatus({ code: api.SpanStatusCode.ERROR });
                span?.end();
                throw error;
              });
          }
          
          span?.end();
          return result;
        } catch (error) {
          span?.recordException(error as Error);
          span?.setStatus({ code: api.SpanStatusCode.ERROR });
          span?.end();
          throw error;
        }
      }
    );
  }) as T;
}

/**
 * Get trace context from current span
 */
export function getTraceContext(): {
  traceId?: string;
  spanId?: string;
  traceFlags?: number;
} {
  const span = api.trace.getSpan(api.context.active());
  if (!span) {
    return {};
  }

  const spanContext = span.spanContext();
  return {
    traceId: spanContext.traceId,
    spanId: spanContext.spanId,
    traceFlags: spanContext.traceFlags,
  };
}

/**
 * Inject trace context into headers
 * For propagating trace context to downstream services
 */
export function injectTraceContext(headers: Record<string, string> = {}): Record<string, string> {
  const context = getTraceContext();
  
  if (context.traceId) {
    headers['x-trace-id'] = context.traceId;
  }
  
  if (context.spanId) {
    headers['x-span-id'] = context.spanId;
  }
  
  return headers;
}

/**
 * Extract trace context from headers
 * For receiving trace context from upstream services
 */
export function extractTraceContext(headers: Record<string, string | string[] | undefined>): {
  traceId?: string;
  spanId?: string;
} {
  const traceId = Array.isArray(headers['x-trace-id'])
    ? headers['x-trace-id'][0]
    : headers['x-trace-id'];
  const spanId = Array.isArray(headers['x-span-id'])
    ? headers['x-span-id'][0]
    : headers['x-span-id'];

  return {
    traceId,
    spanId,
  };
}
