import { NextRequest, NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { platform, saveTokens } from '@/lib/social';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Manual connect: the operator pastes a token they ALREADY HOLD for an account they own (useful
// when a full OAuth app isn't set up). We only store it (sealed). By submitting, the operator
// attests the account is theirs — the mascot posts only to owned channels.
export async function POST(req: NextRequest, { params }: { params: { platform: string } }) {
  const unauth = await requireConsole();
  if (unauth) return unauth;

  const p = platform(params.platform);
  if (!p) return NextResponse.json({ error: 'unknown platform' }, { status: 404 });
  if (!p.manualOk) return NextResponse.json({ error: 'manual connect not supported for this platform' }, { status: 400 });

  let body: any = {};
  try { body = await req.json(); } catch {}
  const accessToken = typeof body?.accessToken === 'string' ? body.accessToken.trim() : '';
  const handle = typeof body?.handle === 'string' ? body.handle.trim().slice(0, 80) : '';
  const refreshToken = typeof body?.refreshToken === 'string' ? body.refreshToken.trim() : '';
  const owned = body?.ownedAttestation === true;

  if (!accessToken) return NextResponse.json({ error: 'accessToken required' }, { status: 400 });
  if (!owned) return NextResponse.json({ error: 'you must attest the account is yours (ownedAttestation)' }, { status: 400 });

  await saveTokens(p.key, {
    accessToken, refreshToken: refreshToken || undefined, handle, displayName: handle,
    scope: p.scope, meta: { via: 'manual', ownedAttestation: true },
  });
  return NextResponse.json({ ok: true, platform: p.key, handle });
}
