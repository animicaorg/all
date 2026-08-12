import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/ai/[slug]/ask -> 410 GONE. Metered per-listing AI chat is retired with the
// AI marketplace. Its successor is a deployed Animica Python Cloud FUNCTION: the developer
// ships Python (which may call AI via the runtime's capability broker), it is anchored
// on-chain by a DEPLOY tx and executed off-chain in the hardened sandbox, and callers invoke
// it at a stable public URL that meters and settles in ANM exactly like /ask did (80/20).
// This was a documented public API, so agents get a machine-readable pointer, not a 404.
export async function POST(_req: Request, { params }: { params: { slug: string } }) {
  return NextResponse.json(
    {
      error: {
        code: 'gone',
        message:
          `The AI marketplace is retired; '${params.slug}' is no longer served here. ` +
          'Invoke an Animica Python Cloud function instead: POST /api/cloud/v1/fn/{owner}/{slug}.',
        details: {
          successor: 'animica-python-cloud',
          invoke: { method: 'POST', path: '/api/cloud/v1/fn/{owner}/{slug}' },
          create: { method: 'POST', path: '/api/cloud/v1/functions' },
          browse: '/apps',
        },
      },
    },
    { status: 410 },
  );
}
