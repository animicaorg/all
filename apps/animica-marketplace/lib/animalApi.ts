import { NextRequest, NextResponse } from 'next/server';
import { isConsoleAuthed } from './animalAuth';
import { constEq } from './vault';

// Small shared helpers for the Animica Animal API surface.

export function clientIp(req: NextRequest): string {
  // Our only ingress is local nginx, which sets X-Forwarded-For = "$proxy_add_x_forwarded_for"
  // (any client-supplied chain, then the REAL remote_addr appended LAST). So the trustworthy,
  // un-spoofable client IP is the LAST entry — using the first would let an attacker rotate a fake
  // X-Forwarded-For to evade the per-IP login lockout. Prefer X-Real-IP (nginx sets it to
  // $remote_addr) when present.
  const real = req.headers.get('x-real-ip');
  if (real) return real.trim();
  const xff = req.headers.get('x-forwarded-for');
  if (xff) {
    const parts = xff.split(',').map((s) => s.trim()).filter(Boolean);
    if (parts.length) return parts[parts.length - 1];
  }
  return 'unknown';
}

// Operator-console gate for browser routes.
export async function requireConsole(): Promise<NextResponse | null> {
  if (!(await isConsoleAuthed())) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  return null;
}

// Bearer gate for the localhost posting engine. FAIL-CLOSED: if ANIMAL_INTERNAL_TOKEN is unset,
// the internal surface is disabled entirely (503), never open.
export function requireInternal(req: NextRequest): NextResponse | null {
  const expected = process.env.ANIMAL_INTERNAL_TOKEN || '';
  if (!expected) {
    return NextResponse.json({ error: 'internal endpoint disabled (ANIMAL_INTERNAL_TOKEN unset)' }, { status: 503 });
  }
  const auth = req.headers.get('authorization') || '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7).trim() : '';
  if (!constEq(token, expected)) {
    return NextResponse.json({ error: 'forbidden' }, { status: 403 });
  }
  return null;
}

export function baseUrl(): string {
  return process.env.PUBLIC_BASE_URL || 'https://animica.dev';
}
