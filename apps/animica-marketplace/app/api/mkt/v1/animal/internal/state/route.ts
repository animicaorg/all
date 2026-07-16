import { NextRequest, NextResponse } from 'next/server';
import { requireInternal } from '@/lib/animalApi';
import { connectionsForEngine } from '@/lib/social';
import { getCharacter } from '@/lib/animalCharacter';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// INTERNAL, localhost-only (Bearer ANIMAL_INTERNAL_TOKEN, fail-closed). Returns everything the
// posting engine needs for a cycle: connected channels WITH unsealed tokens (the only path that
// unseals them), the active steering directives, and the operator's pause flag. This route must
// never be exposed to the public internet — it is proxied only on 127.0.0.1.
export async function GET(req: NextRequest) {
  const bad = requireInternal(req);
  if (bad) return bad;

  const [channels, directives, st, character] = await Promise.all([
    connectionsForEngine(),
    prisma.animalDirective.findMany({ where: { active: true }, orderBy: { createdAt: 'desc' }, take: 40 }),
    prisma.animalEngineState.findUnique({ where: { id: 'animal' } }),
    getCharacter(),
  ]);

  return NextResponse.json({
    channels: channels.filter((c) => c.autoPost),
    directives: directives.reverse().map((d) => ({ role: d.role, kind: d.kind, text: d.text, at: d.createdAt })),
    paused: st?.paused ?? false,
    forceDryRun: st?.forceDryRun ?? false, // operator override — engine must dry-run if true
    // The live-editable mascot the stream worker renders + role-plays.
    character,
    // Google OAuth app creds for the livestream worker to refresh tokens. This route is
    // Bearer-gated AND edge-denied (127.0.0.1 only), so the secret never reaches the internet.
    youtube: {
      clientId: process.env.YOUTUBE_CLIENT_ID || '',
      clientSecret: process.env.YOUTUBE_CLIENT_SECRET || '',
    },
  });
}
