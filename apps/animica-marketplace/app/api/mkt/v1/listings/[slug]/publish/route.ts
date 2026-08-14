import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/listings/[slug]/publish -> 410 GONE. This was the AI-listing publish step
// (DRAFT|DELISTED -> PUBLISHED + version snapshot) and is retired with the AI marketplace.
// Store apps/games never published here — the Game Lab / Developer Center flow publishes via
// PATCH /api/mkt/v1/store/apps/[slug] {status: PUBLISHED}, which is untouched. Documented
// public API, so no framework 404: callers get a machine-readable pointer to the successor —
// on Animica Python Cloud a function goes live by DEPLOYING it (anchored on-chain via a
// DEPLOY tx, executed off-chain by the Cloud), not by flipping a listing status.
export async function POST() {
  return NextResponse.json(
    {
      error: {
        code: 'gone',
        message:
          'AI-listing publishing is retired. Deploy a Python function on Animica Python Cloud instead: ' +
          'POST /api/cloud/v1/functions (then invoke it at /api/cloud/v1/fn/{owner}/{slug}).',
        details: {
          successor: 'animica-python-cloud',
          create: { method: 'POST', path: '/api/cloud/v1/functions' },
          invoke: { method: 'POST', path: '/api/cloud/v1/fn/{owner}/{slug}' },
          browse: '/apps',
          store_publish_unchanged: { method: 'PATCH', path: '/api/mkt/v1/store/apps/{slug}' },
        },
      },
    },
    { status: 410 },
  );
}
