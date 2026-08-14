import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/media/[slug]/preview -> 410 GONE. The free low-res per-listing media
// preview is retired with the AI marketplace. The media queue itself lives on at
// /api/mkt/v1/media/jobs; monetized media products are now Animica Python Cloud functions.
// Documented public API — machine-readable tombstone, not a framework 404.
export async function POST(_req: Request, { params }: { params: { slug: string } }) {
  return NextResponse.json(
    {
      error: {
        code: 'gone',
        message:
          `The AI marketplace is retired; media listing '${params.slug}' has no preview here. ` +
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
