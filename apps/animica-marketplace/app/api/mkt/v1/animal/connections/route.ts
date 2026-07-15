import { NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { listConnectionsPublic } from '@/lib/social';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  return NextResponse.json({ connections: await listConnectionsPublic() });
}
