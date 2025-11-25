/**
 * Host permissions (allow/deny) with wildcard support.
 *
 * Patterns supported:
 *   - Exact origin: "https://studio.animica.dev"
 *   - With explicit port: "http://localhost:3000"
 *   - Wildcard subdomains: "https://*.animica.dev"
 *   - Host-only (any scheme): "localhost", "*.example.com"
 *   - Any port: "http://localhost:*" or "localhost:*"
 *   - Any scheme: "*.example.com:*" (omit scheme) or "*://*.example.com"
 *
 * Storage keys:
 *  - "hosts.allowlist.v1": string[]
 *  - "hosts.denylist.v1": string[]
 *
 * Default policy:
 *  - Denylist always wins.
 *  - If allowlist is empty, a dev-safe allowlist is applied:
 *      http(s)://localhost:* and http(s)://127.0.0.1:*
 *  - If allowlist is non-empty, only matches in allowlist are allowed.
 */

import { storage } from "./runtime";

const KEY_ALLOW = "hosts.allowlist.v1";
const KEY_DENY = "hosts.denylist.v1";

export type HostPattern = string;
export type DefaultPolicy = "allow-dev-local" | "deny-all" | "allow-all";

/* -------------------------------- defaults -------------------------------- */

const DEV_DEFAULTS: HostPattern[] = [
  "http://localhost:*",
  "https://localhost:*",
  "http://127.0.0.1:*",
  "https://127.0.0.1:*",
];

function broadcast(type: string, payload: any): void {
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore
  if (chrome?.runtime?.sendMessage) {
    chrome.runtime.sendMessage({ __animica: true, type, payload });
  }
}

/* --------------------------------- API ------------------------------------ */

export async function getAllowlist(): Promise<HostPattern[]> {
  return (await storage.get<HostPattern[] | null>(KEY_ALLOW)) ?? [];
}

export async function getDenylist(): Promise<HostPattern[]> {
  return (await storage.get<HostPattern[] | null>(KEY_DENY)) ?? [];
}

export async function setAllowlist(patterns: HostPattern[]): Promise<void> {
  await storage.set(KEY_ALLOW, normalizePatterns(patterns));
  broadcast("hosts:allowlistUpdated", {});
}

export async function setDenylist(patterns: HostPattern[]): Promise<void> {
  await storage.set(KEY_DENY, normalizePatterns(patterns));
  broadcast("hosts:denylistUpdated", {});
}

export async function addAllow(pattern: HostPattern): Promise<void> {
  const list = await getAllowlist();
  if (!list.includes(pattern)) {
    list.push(pattern);
    await setAllowlist(list);
  }
}

export async function removeAllow(pattern: HostPattern): Promise<void> {
  const list = await getAllowlist();
  const next = list.filter((p) => p !== pattern);
  await setAllowlist(next);
}

export async function addDeny(pattern: HostPattern): Promise<void> {
  const list = await getDenylist();
  if (!list.includes(pattern)) {
    list.push(pattern);
    await setDenylist(list);
  }
}

export async function removeDeny(pattern: HostPattern): Promise<void> {
  const list = await getDenylist();
  const next = list.filter((p) => p !== pattern);
  await setDenylist(next);
}

/**
 * Resets allow+deny to empty (allowlist empty ⇒ dev defaults apply).
 */
export async function resetToDefaults(): Promise<void> {
  await storage.set(KEY_ALLOW, []);
  await storage.set(KEY_DENY, []);
  broadcast("hosts:resetDefaults", {});
}

/**
 * Check whether an origin (or URL) is allowed per lists and policy.
 *
 * @param originOrUrl - "https://site.tld" or full URL like "https://site.tld/page"
 * @param policy - default behavior when allowlist is empty:
 *    - "allow-dev-local" (default): allow localhost/127.0.0.1
 *    - "deny-all": require explicit allowlist
 *    - "allow-all": allow unless denied
 */
export async function isOriginAllowed(
  originOrUrl: string,
  policy: DefaultPolicy = "allow-dev-local",
): Promise<boolean> {
  const origin = toOrigin(originOrUrl);
  if (!origin) return false;

  const deny = await getDenylist();
  if (matchesAny(origin, deny)) return false;

  const allow = await getAllowlist();
  if (allow.length > 0) {
    return matchesAny(origin, allow);
  }

  switch (policy) {
    case "deny-all":
      return false;
    case "allow-all":
      return true;
    case "allow-dev-local":
    default:
      return matchesAny(origin, DEV_DEFAULTS);
  }
}

/**
 * Returns true if origin matches denylist; useful for UI badges.
 */
export async function isOriginDenied(originOrUrl: string): Promise<boolean> {
  const origin = toOrigin(originOrUrl);
  if (!origin) return true;
  const deny = await getDenylist();
  return matchesAny(origin, deny);
}

/* -------------------------------- matching -------------------------------- */

function normalizePatterns(ps: HostPattern[]): HostPattern[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of ps) {
    const s = (p || "").trim();
    if (s && !seen.has(s)) {
      seen.add(s);
      out.push(s);
    }
  }
  return out;
}

function toOrigin(input: string): string | null {
  try {
    // If it's already an origin string (scheme://host[:port]), URL() will preserve origin.
    const u = new URL(input);
    return u.origin;
  } catch {
    // Might be host-only pattern. We cannot evaluate it to an origin,
    // but callers of isOriginAllowed should pass a concrete origin/URL.
    return null;
  }
}

function effectivePort(u: URL): string {
  if (u.port) return u.port;
  const scheme = u.protocol.replace(":", "");
  if (scheme === "http") return "80";
  if (scheme === "https") return "443";
  return ""; // unknown / extension schemes won't be used here
}

function parsePattern(pattern: string): {
  scheme: string | "*" | null;
  host: string; // without wildcard prefix
  wildcard: boolean;
  port: string | "*" | null;
} {
  let scheme: string | "*" | null = null;
  let hostPort = pattern;

  // Accept "*://host" to mean any scheme
  if (pattern.startsWith("*://")) {
    scheme = "*";
    hostPort = pattern.slice(4);
  } else if (pattern.includes("://")) {
    try {
      const u = new URL(pattern);
      scheme = u.protocol.replace(":", "");
      hostPort = u.host; // host[:port]
    } catch {
      // fall back to host-only parse
      scheme = null;
      hostPort = pattern.split("://")[1] || pattern;
    }
  }

  let port: string | "*" | null = null;
  let host = hostPort;

  // Detect trailing :port or :*
  const idx = hostPort.lastIndexOf(":");
  if (idx > -1 && !hostPort.endsWith("]")) {
    const maybePort = hostPort.slice(idx + 1);
    if (maybePort === "*" || /^\d+$/.test(maybePort)) {
      port = maybePort;
      host = hostPort.slice(0, idx);
    }
  }

  let wildcard = false;
  if (host.startsWith("*.")) {
    wildcard = true;
    host = host.slice(2);
  }

  return { scheme, host: host.toLowerCase(), wildcard, port };
}

function matchPattern(pattern: string, origin: string): boolean {
  let u: URL;
  try {
    u = new URL(origin);
  } catch {
    return false;
  }
  const targetScheme = u.protocol.replace(":", "");
  const targetHost = u.hostname.toLowerCase();
  const targetPort = effectivePort(u);

  const p = parsePattern(pattern);

  // Scheme
  if (p.scheme && p.scheme !== "*" && p.scheme !== targetScheme) {
    return false;
  }

  // Host
  if (p.wildcard) {
    // "*.example.com" matches subdomains but not apex.
    if (targetHost === p.host) return false;
    if (!targetHost.endsWith("." + p.host)) return false;
  } else {
    if (targetHost !== p.host) return false;
  }

  // Port
  if (p.port && p.port !== "*" && p.port !== targetPort) {
    return false;
  }

  return true;
}

function matchesAny(origin: string, patterns: string[]): boolean {
  for (const p of patterns) {
    if (matchPattern(p, origin)) return true;
  }
  return false;
}

/* ------------------------------- convenience ------------------------------- */

/**
 * Ensure a given origin is permitted by adding to allowlist (idempotent).
 * Useful after an explicit user approval for a site.
 */
export async function permitOrigin(originOrUrl: string, withPortWildcard = true): Promise<void> {
  const origin = toOrigin(originOrUrl);
  if (!origin) throw new Error("Invalid origin/URL");
  const u = new URL(origin);
  const pattern = withPortWildcard ? `${u.protocol}//${u.hostname}:*` : origin;
  await addAllow(pattern);
}

/**
 * Explicitly block an origin by adding a specific pattern to denylist.
 */
export async function blockOrigin(originOrUrl: string, withPortWildcard = true): Promise<void> {
  const origin = toOrigin(originOrUrl);
  if (!origin) throw new Error("Invalid origin/URL");
  const u = new URL(origin);
  const pattern = withPortWildcard ? `${u.protocol}//${u.hostname}:*` : origin;
  await addDeny(pattern);
}

export default {
  getAllowlist,
  getDenylist,
  setAllowlist,
  setDenylist,
  addAllow,
  removeAllow,
  addDeny,
  removeDeny,
  resetToDefaults,
  isOriginAllowed,
  isOriginDenied,
  permitOrigin,
  blockOrigin,
};
