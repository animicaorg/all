const LOCALHOSTS = new Set(["localhost", "127.0.0.1"]);
const API_PREFIX = "/api/v1";

function stripTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

function ensureApiPrefix(rawUrl: string): string {
  const normalized = stripTrailingSlash(rawUrl);

  try {
    const parsed = new URL(normalized);

    if (LOCALHOSTS.has(parsed.hostname)) {
      return normalized;
    }

    const pathname = stripTrailingSlash(parsed.pathname);
    if (pathname === API_PREFIX || pathname.endsWith(API_PREFIX)) {
      return normalized;
    }

    if (pathname === "") {
      return `${normalized}${API_PREFIX}`;
    }

    return normalized;
  } catch {
    // Keep env-provided non-URL values untouched.
    return normalized;
  }
}

export function getApiBaseUrl(): string {
  const configuredUrl = import.meta.env.VITE_CEX_API_URL?.trim();
  if (configuredUrl) {
    return ensureApiPrefix(configuredUrl);
  }

  if (typeof window === "undefined") {
    return "http://localhost:3000";
  }

  const { protocol, hostname, host } = window.location;
  if (LOCALHOSTS.has(hostname)) {
    return "http://localhost:3000";
  }

  // Production frontend is hosted on trade.animica.org while API lives on api.animica.io.
  // Route browser API traffic directly to API host to avoid relying on frontend reverse-proxy config.
  if (hostname === "trade.animica.org") {
    return `${protocol}//api.animica.io${API_PREFIX}`;
  }

  return `${protocol}//${host}${API_PREFIX}`;
}

export function getWsUrl(): string {
  const configuredUrl = import.meta.env.VITE_CEX_WS_URL?.trim();
  if (configuredUrl) {
    return configuredUrl;
  }

  if (typeof window === "undefined") {
    return "ws://localhost:3000/ws";
  }

  const { protocol, hostname, host } = window.location;
  if (LOCALHOSTS.has(hostname)) {
    return "ws://localhost:3000/ws";
  }

  if (hostname === "trade.animica.org") {
    const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//api.animica.io/ws`;
  }

  const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${host}/ws`;
}
