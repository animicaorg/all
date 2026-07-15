import { NextRequest, NextResponse } from 'next/server';
import { baseUrl } from '@/lib/animalApi';
import { isConsoleAuthed } from '@/lib/animalAuth';
import { prisma } from '@/lib/db';
import { platform, exchangeCode, saveTokens } from '@/lib/social';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Fixed, hard-coded reasons only. We NEVER reflect provider `error` params or exception strings
// into the page (that was a reflected-XSS same-origin-takeover vector). The page carries a strict
// CSP and uses a meta-refresh (no inline script) so even a mistake can't execute injected markup.
type Reason = 'not_signed_in' | 'unknown_platform' | 'provider_denied' | 'bad_request' | 'state_invalid' | 'state_expired' | 'no_token' | 'exchange_failed';
const REASON_TEXT: Record<Reason, string> = {
  not_signed_in: 'You are not signed in to the console.',
  unknown_platform: 'Unknown platform.',
  provider_denied: 'The provider denied or cancelled the authorization.',
  bad_request: 'The authorization response was incomplete.',
  state_invalid: 'Invalid or expired sign-in state.',
  state_expired: 'The sign-in link expired — please try again.',
  no_token: 'The provider did not return an access token.',
  exchange_failed: 'Could not complete the token exchange.',
};

function done(ok: boolean, reason?: Reason): NextResponse {
  const q = ok ? 'connected' : 'error';
  const heading = ok ? '✓ Connected' : '⚠ ' + (reason ? REASON_TEXT[reason] : 'Connection failed');
  const html = `<!doctype html><html lang=en><head><meta charset=utf-8>` +
    `<meta http-equiv="refresh" content="1.4;url=/animal?${q}=1">` +
    `<title>${ok ? 'Connected' : 'Connection failed'}</title></head>` +
    `<body style="font-family:system-ui;background:#0b0b12;color:#eee;display:grid;place-items:center;height:100vh;margin:0">` +
    `<div style="text-align:center"><h2>${heading}</h2>` +
    `<p><a style="color:#8bd" href="/animal?${q}=1">Return to the Animica Animal console</a></p></div></body></html>`;
  return new NextResponse(html, {
    status: 200,
    headers: {
      'content-type': 'text/html; charset=utf-8',
      // Even though nothing dynamic is interpolated, lock the page down defensively.
      'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'",
      'referrer-policy': 'no-referrer',
      'x-content-type-options': 'nosniff',
    },
  });
}

// OAuth callback. Verifies the one-time state (CSRF/PKCE), exchanges the code for tokens, seals and
// stores them. Requires an active console session so a stray callback can't bind an account.
export async function GET(req: NextRequest, { params }: { params: { platform: string } }) {
  if (!(await isConsoleAuthed())) return done(false, 'not_signed_in');

  const p = platform(params.platform);
  if (!p) return done(false, 'unknown_platform');

  const url = new URL(req.url);
  const code = url.searchParams.get('code') || '';
  const state = url.searchParams.get('state') || '';
  const err = url.searchParams.get('error');
  if (err) return done(false, 'provider_denied');       // do NOT reflect the provider's error text
  if (!code || !state) return done(false, 'bad_request');

  const st = await prisma.oAuthState.findUnique({ where: { state } });
  if (!st || st.platform !== p.key) return done(false, 'state_invalid');
  // One-time use.
  await prisma.oAuthState.delete({ where: { id: st.id } }).catch(() => {});
  // Reject stale states (>15 min).
  if (Date.now() - st.createdAt.getTime() > 15 * 60 * 1000) return done(false, 'state_expired');

  try {
    const redirectUri = `${baseUrl()}/api/mkt/v1/animal/connect/${p.key}/callback`;
    const tok = await exchangeCode(p, { code, verifier: st.verifier || undefined, redirectUri });
    if (!tok.accessToken) return done(false, 'no_token');
    await saveTokens(p.key, {
      accessToken: tok.accessToken, refreshToken: tok.refreshToken,
      expiresIn: tok.expiresIn, scope: tok.scope, meta: { via: 'oauth' },
    });
    return done(true);
  } catch {
    // Never surface the exception text to the browser page.
    return done(false, 'exchange_failed');
  }
}
