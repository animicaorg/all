import { NextRequest, NextResponse } from 'next/server';
import { randomBytes } from 'node:crypto';
import { requireConsole, baseUrl } from '@/lib/animalApi';
import { prisma } from '@/lib/db';
import { platform, isConfigured, buildAuthorizeUrl, pkcePair } from '@/lib/social';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Begin an OAuth connect for an OWNED account. If the platform's OAuth app credentials are not
// configured, return an honest "not configured" payload (with the manual-token alternative) rather
// than a broken redirect. Never creates an account — only links one the operator already controls.
export async function GET(req: NextRequest, { params }: { params: { platform: string } }) {
  const unauth = await requireConsole();
  if (unauth) return unauth;

  const p = platform(params.platform);
  if (!p) return NextResponse.json({ error: 'unknown platform' }, { status: 404 });

  if (!isConfigured(p)) {
    return NextResponse.json({
      configured: false,
      platform: p.key,
      message: `OAuth app for ${p.label} is not configured. Set ${p.clientIdEnv} and ${p.clientSecretEnv}` +
        (p.key === 'mastodon' ? ' and MASTODON_INSTANCE' : '') +
        `, or connect a token you already hold for your owned account.`,
      manualOk: p.manualOk,
    }, { status: 200 });
  }

  const state = randomBytes(24).toString('base64url');
  const { verifier, challenge } = p.pkce ? pkcePair() : { verifier: '', challenge: '' };
  await prisma.oAuthState.create({ data: { platform: p.key, state, verifier } });

  const redirectUri = `${baseUrl()}/api/mkt/v1/animal/connect/${p.key}/callback`;
  const url = buildAuthorizeUrl(p, { state, challenge: challenge || undefined, redirectUri });
  return NextResponse.json({ configured: true, authorizeUrl: url });
}
