// API-key auth for the OpenAI-compatible surface.
//
// Keys look like `anm_live_<random>`. Only the sha256 hash is stored (same
// discipline as Session tokens); the raw secret is returned ONCE at mint time.
// A new key's owner gets a small starter credit grant on their first key so
// the API is immediately callable in dev/demo.

import type { ApiKey, User } from "@prisma/client";
import { hashToken, newOpaqueToken } from "@/lib/tokens";
import { grantCredit } from "@/server/credit";
import { prisma } from "@/server/db";
import { env } from "@/server/env";

const KEY_PREFIX = "anm_live_";

export interface MintedKey {
  id: string;
  raw: string; // shown once, never stored
  prefix: string; // safe to display
}

export async function mintApiKey(userId: string, label?: string): Promise<MintedKey> {
  const raw = `${KEY_PREFIX}${newOpaqueToken(24)}`;
  const prefix = raw.slice(0, KEY_PREFIX.length + 4); // e.g. anm_live_AbC1
  const keyHash = hashToken(raw);

  const existing = await prisma.apiKey.count({ where: { userId } });
  const created = await prisma.apiKey.create({
    data: { userId, keyHash, prefix, label: label ?? null },
  });

  // First key → starter grant so it can call the API right away.
  if (existing === 0) {
    const grant = env().DEFAULT_CREDIT_GRANT_USD;
    if (grant && grant !== "0") {
      await grantCredit({
        userId,
        amountUsd: grant,
        source: "grant",
        note: "starter grant on first API key",
      });
    }
  }

  return { id: created.id, raw, prefix };
}

export interface AuthedKey {
  user: User;
  apiKey: ApiKey;
}

/** Resolve a Bearer key to its owner, or null if missing/invalid/revoked. */
export async function authenticateApiKey(req: Request): Promise<AuthedKey | null> {
  const header = req.headers.get("authorization") ?? "";
  const m = header.match(/^Bearer\s+(.+)$/i);
  if (!m) return null;
  const raw = m[1].trim();
  if (!raw.startsWith(KEY_PREFIX)) return null;

  const apiKey = await prisma.apiKey.findUnique({
    where: { keyHash: hashToken(raw) },
    include: { user: true },
  });
  if (!apiKey || !apiKey.isActive) return null;
  if (apiKey.user.role === "BANNED") return null;

  // Best-effort last-used stamp; never block the request on it.
  void prisma.apiKey
    .update({ where: { id: apiKey.id }, data: { lastUsedAt: new Date() } })
    .catch(() => {});

  return { user: apiKey.user, apiKey };
}

export function revokeApiKey(id: string): Promise<unknown> {
  return prisma.apiKey.update({
    where: { id },
    data: { isActive: false, revokedAt: new Date() },
  });
}
