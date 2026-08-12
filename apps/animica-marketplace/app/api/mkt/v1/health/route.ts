import { prisma } from '@/lib/db';
import { getHead } from '@/lib/chain';
import { ok } from '@/lib/api';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/health -> liveness + dependency check (does NOT reuse the dead /health on :8792).
export async function GET() {
  const out: any = { service: 'animica-marketplace', ok: true, ts: new Date().toISOString() };
  try { await prisma.$queryRaw`SELECT 1`; out.db = 'ok'; } catch (e: any) { out.db = `error: ${e.message}`; out.ok = false; }
  try { const h = await getHead(); out.chainHeight = h.height; } catch (e: any) { out.chain = `error: ${e.message}`; }
  return ok(out);
}
