import crypto from "node:crypto";

export const MAX_TEXT = 280;

export function sanitizeText(value, maxLen = MAX_TEXT) {
  const raw = String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").trim();
  return raw.slice(0, maxLen);
}

export function sanitizeDescription(value) {
  return sanitizeText(value, 2000);
}

export function sanitizeUrl(value) {
  const trimmed = sanitizeText(value, 512);
  if (!trimmed) return "";
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") return "";
    return url.toString();
  } catch {
    return "";
  }
}

export function sha256Hex(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

export function toIsoNow() {
  return new Date().toISOString();
}

export function bigintToString(value) {
  if (typeof value === "bigint") return value.toString();
  return String(value ?? "0");
}

export function parsePositiveInt(value, field) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) {
    throw new Error(`Invalid ${field}`);
  }
  return Math.floor(n);
}

export function isNativeToken(value) {
  const v = String(value || "").trim().toLowerCase();
  return v === "anm" || v === "native" || v === "";
}

export function normalizeTokenAddress(value) {
  if (isNativeToken(value)) return "ANM";
  return String(value || "").trim();
}

export function findPoolByTokens(pools, tokenA, tokenB) {
  const a = normalizeTokenAddress(tokenA);
  const b = normalizeTokenAddress(tokenB);
  return pools.find((pool) => {
    const pa = normalizeTokenAddress(pool.tokenAAddress);
    const pb = normalizeTokenAddress(pool.tokenBAddress);
    return (pa === a && pb === b) || (pa === b && pb === a);
  });
}
