import { NextRequest } from 'next/server';
import { ok, err, withCredentialedCors, credentialedPreflight } from '@/lib/api';
import { clearSessionCookie } from '@/lib/session';

export const dynamic = 'force-dynamic';

// Preflight for the cross-origin Game Lab publish flow signing out (animica.io).
export async function OPTIONS(req: NextRequest) {
  return credentialedPreflight(req, 'POST, OPTIONS');
}

// POST /api/mkt/v1/auth/logout -> clear the httpOnly session cookie.
// The cookie is httpOnly, so only the server can drop it; the dev-portal (and any
// wallet-login UI) calls this to sign out.
export async function POST(req: NextRequest) {
  try {
    clearSessionCookie();
    return withCredentialedCors(req, ok({ ok: true }));
  } catch (e) {
    return withCredentialedCors(req, err(e));
  }
}
