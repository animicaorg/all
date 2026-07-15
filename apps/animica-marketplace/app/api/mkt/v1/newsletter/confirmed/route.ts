import { NextRequest, NextResponse } from 'next/server';
import { confirmedRecipients } from '@/lib/newsletter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// INTERNAL recipient source for the gated Python sender (same box, 127.0.0.1:4950). Bearer-gated
// with GROWTH_INTERNAL_TOKEN and fail-closed: if the token is unset, the endpoint is disabled, so
// the confirmed list is never accidentally exposed. Returns CONFIRMED-minus-suppressed recipients
// with a per-recipient one-click unsubscribe token. There is no way to pass in a recipient list.
export async function GET(req: NextRequest) {
  const want = process.env.GROWTH_INTERNAL_TOKEN;
  if (!want) return NextResponse.json({ error: 'disabled' }, { status: 503 });
  const auth = req.headers.get('authorization') || '';
  if (auth !== `Bearer ${want}`) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  const recipients = await confirmedRecipients();
  return NextResponse.json({ count: recipients.length, recipients });
}
