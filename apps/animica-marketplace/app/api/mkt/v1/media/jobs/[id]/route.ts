import { NextRequest, NextResponse } from 'next/server';
import { publicOk, publicPreflight, PUBLIC_CORS } from '@/lib/api';
import { pollJob } from '@/lib/mediaQueue';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export function OPTIONS() {
  return publicPreflight();
}

// Poll a media job. Public read (native .anm sites + the homepage studios poll this).
// Never returns the private input; a private DONE result is delivered exactly once then wiped.
export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  const job = await pollJob(params.id);
  if (!job) {
    return NextResponse.json({ error: { code: 'not_found', message: 'No such media job.' } }, { status: 404, headers: PUBLIC_CORS });
  }
  return publicOk(job);
}
