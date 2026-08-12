import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/media/[slug]/generate -> 410 GONE. Per-LISTING metered media generation is
// retired with the AI marketplace. The GPU-miner media queue itself is NOT retired — raw
// generation still runs via the media jobs API (/api/mkt/v1/media/jobs and the OpenAI-compat
// /api/mkt/v1/media/openai/*), and a monetized media product is now a deployed Animica Python
// Cloud function invoked at its public URL. Documented public API — tombstone, not a 404.
export async function POST(_req: Request, { params }: { params: { slug: string } }) {
  return NextResponse.json(
    {
      error: {
        code: 'gone',
        message:
          `The AI marketplace is retired; media listing '${params.slug}' is no longer served here. ` +
          'Use the media jobs API directly, or invoke an Animica Python Cloud function: ' +
          'POST /api/cloud/v1/fn/{owner}/{slug}.',
        details: {
          successor: 'animica-python-cloud',
          invoke: { method: 'POST', path: '/api/cloud/v1/fn/{owner}/{slug}' },
          media_jobs_unchanged: { method: 'POST', path: '/api/mkt/v1/media/jobs' },
          browse: '/apps',
        },
      },
    },
    { status: 410 },
  );
}
