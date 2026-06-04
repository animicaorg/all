// Lightweight Setting cache.
//
// Reads the Setting table with a small TTL so the hot quote path doesn't
// hammer Postgres. Admin updates explicitly invalidate the cache.

import { prisma } from "./db";

const TTL_MS = 5_000;
let cached: { at: number; values: Record<string, string> } | null = null;

async function loadAll(): Promise<Record<string, string>> {
  const rows = await prisma.setting.findMany();
  const out: Record<string, string> = {};
  for (const r of rows) out[r.key] = r.value;
  return out;
}

export async function getSettings(): Promise<Record<string, string>> {
  const now = Date.now();
  if (cached && now - cached.at < TTL_MS) return cached.values;
  const values = await loadAll();
  cached = { at: now, values };
  return values;
}

export async function getSetting(key: string, defaultValue = ""): Promise<string> {
  const all = await getSettings();
  return all[key] ?? defaultValue;
}

export async function getNumber(key: string, defaultValue = 0): Promise<number> {
  const v = await getSetting(key, String(defaultValue));
  const n = Number(v);
  return Number.isFinite(n) ? n : defaultValue;
}

export async function getBool(key: string, defaultValue = false): Promise<boolean> {
  const v = await getSetting(key, String(defaultValue));
  return v === "true" || v === "1";
}

export async function getList(key: string): Promise<string[]> {
  const v = await getSetting(key, "");
  return v
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export async function setSetting(
  key: string,
  value: string,
): Promise<void> {
  await prisma.setting.upsert({
    where: { key },
    update: { value },
    create: { key, value },
  });
  cached = null;     // invalidate
}

export function invalidateSettingsCache() {
  cached = null;
}
