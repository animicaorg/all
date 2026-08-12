import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/ai/[slug]/preview -> 410 GONE. The free preview turns for AI listings are
// retired with the AI marketplace. On Animica Python Cloud a developer prices their own
// function (free-tier invocations are the preview mechanism); invoke at the function's public
// URL. Documented public API — machine-readable tombstone, not a framework 404.
export async function POST(_req: Request, { params }: { params: { slug: string } }) {
  return NextResponse.json(
    {
      error: {
        code: 'gone',
        message:
          `The AI marketplace is retired; '${params.slug}' has no preview here. ` +
          'Invoke an Animica Python Cloud function instead: POST /api/cloud/v1/fn/{owner}/{slug}.',
        details: {
          successor: 'animica-python-cloud',
          invoke: { method: 'POST', path: '/api/cloud/v1/fn/{owner}/{slug}' },
          browse: '/apps',
        },
      },
    },
    { status: 410 },
  );
}
