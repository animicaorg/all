export const hasWindow = typeof window !== 'undefined';
export const g = globalThis as any;

export const fetchFn: typeof fetch | undefined =
  typeof g.fetch === 'function' ? g.fetch.bind(g) : undefined;

export const cryptoObj: Crypto | undefined = g.crypto;

export const setTimeoutFn: typeof setTimeout | undefined =
  typeof g.setTimeout === 'function' ? g.setTimeout.bind(g) : undefined;

export const clearTimeoutFn: typeof clearTimeout | undefined =
  typeof g.clearTimeout === 'function' ? g.clearTimeout.bind(g) : undefined;

export const performanceObj: Performance | undefined = g.performance;
