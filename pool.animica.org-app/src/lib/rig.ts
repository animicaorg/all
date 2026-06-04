// Shared rig identity + address validation. rigId MUST match the pool's
// metrics.rig_id_for(worker, address) = sha256(f"{worker}\x1f{address}").

import { createHash } from "node:crypto";

export function rigIdFor(worker: string, address: string): string {
  return createHash("sha256").update(`${worker}\x1f${address}`).digest("hex");
}

export const ANIM1_RE = /^anim1[0-9a-z]{30,}$/;
export const XMR_RE = /^[48][0-9A-HJ-NP-Za-km-z]{94,105}$/;

export function isAnim1(addr: string): boolean {
  return ANIM1_RE.test(addr.trim());
}
export function isMonero(addr: string): boolean {
  return XMR_RE.test(addr.trim());
}

export type Coins = "ANM" | "XMR" | "BOTH";
export function isCoins(v: string): v is Coins {
  return v === "ANM" || v === "XMR" || v === "BOTH";
}
