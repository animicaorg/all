import { NextRequest, NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { platform, disconnect, setAutoPost } from '@/lib/social';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Disconnect (wipe sealed tokens) or toggle a single channel's autoPost.
export async function POST(req: NextRequest, { params }: { params: { platform: string } }) {
  const unauth = await requireConsole();
  if (unauth) return unauth;

  const p = platform(params.platform);
  if (!p) return NextResponse.json({ error: 'unknown platform' }, { status: 404 });

  let body: any = {};
  try { body = await req.json(); } catch {}

  if (typeof body?.autoPost === 'boolean') {
    await setAutoPost(p.key, body.autoPost);
    return NextResponse.json({ ok: true, autoPost: body.autoPost });
  }
  await disconnect(p.key);
  return NextResponse.json({ ok: true, disconnected: p.key });
}
