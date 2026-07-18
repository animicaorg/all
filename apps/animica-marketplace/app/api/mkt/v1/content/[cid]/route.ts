import { NextRequest, NextResponse } from 'next/server';
import { getObject, isCid } from '@/lib/storage';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/content/[cid] -> raw content bytes by content-address (the replication primitive).
// Any node can fetch a CID to replicate it, then announce a pin. Content is immutable + hash-named,
// so it is cache-forever safe. Returned unaltered so the receiver can verify bytes hash to the CID.
export async function GET(_req: NextRequest, { params }: { params: { cid: string } }) {
  const cid = params.cid;
  if (!isCid(cid)) return NextResponse.json({ error: { code: 'bad_cid', message: 'not a valid anm1c CID' } }, { status: 400 });
  const obj = await getObject(cid);
  if (!obj) return NextResponse.json({ error: { code: 'not_found', message: 'no object for CID' } }, { status: 404 });
  return new NextResponse(new Uint8Array(obj.bytes), {
    status: 200,
    headers: {
      'content-type': obj.mime,
      'content-security-policy': "sandbox allow-scripts allow-forms allow-popups allow-modals",
      'x-content-type-options': 'nosniff',
      'cache-control': 'public, max-age=31536000, immutable',
      'x-anm-cid': cid,
    },
  });
}
