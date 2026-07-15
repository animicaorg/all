import { NextResponse } from 'next/server';
import { publicOk, publicPreflight } from '@/lib/api';
import { stats } from '@/lib/newsletter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export function OPTIONS() { return publicPreflight(); }

// Aggregate, no-PII counts (confirmed/pending/unsubscribed/suppressed) for the growth report.
export async function GET() {
  return publicOk(await stats());
}
