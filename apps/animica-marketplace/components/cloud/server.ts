// Animica Python Cloud — server-side helpers shared by the /cloud console pages.
// Server components only (reads cookies + lib/session, which touch process.env).

import { cookies } from 'next/headers';
import { verifySession } from '@/lib/session';
import { prisma } from '@/lib/db';

export interface CloudSession {
  accountId: string;
  scopes: string[];
}

/**
 * The signed-in developer for a /cloud page, or null.
 *
 * The console requires a session that can actually act as a developer: either a legacy
 * full-rights session ('*') or a purpose=devportal session (scope 'publish'). A buyer 'store'
 * session is deliberately treated as signed-out here — same posture as /dev (DevGate).
 */
export function cloudSession(): CloudSession | null {
  const sess = verifySession(cookies().get('anm_mkt_session')?.value);
  if (!sess) return null;
  if (!sess.scopes.includes('*') && !sess.scopes.includes('publish')) return null;
  return { accountId: sess.accountId, scopes: sess.scopes };
}

/** The {owner} URL segment of a public endpoint: the claimed handle, else the address. */
export function ownerSegment(a: { handle: string | null; address: string }): string {
  return a.handle ?? a.address;
}

/** The signed-in account row the console header + endpoint previews need. */
export async function cloudAccount(accountId: string) {
  return prisma.account.findUnique({
    where: { id: accountId },
    select: { id: true, address: true, handle: true, displayName: true, balanceNanm: true, createdAt: true },
  });
}

/** UTC day key (YYYY-MM-DD) — matches how the analytics endpoint buckets series. */
export function dayKey(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** The last `days` UTC day keys, oldest first, today included. */
export function lastDays(days: number, now = new Date()): string[] {
  const out: string[] = [];
  for (let i = days - 1; i >= 0; i--) {
    out.push(dayKey(new Date(now.getTime() - i * 86_400_000)));
  }
  return out;
}
