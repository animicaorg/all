import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err } from '@/lib/api';
import { serviceDto } from '@/lib/hire';
import { PAYPAL_CLIENT_ID, paypalEnv } from '@/lib/paypal';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Public catalog for the /hire page. Data-driven: whatever rows are active renders as a card.
export async function GET(_req: NextRequest) {
  try {
    const services = await prisma.hireService.findMany({
      where: { active: true },
      orderBy: [{ sortOrder: 'asc' }, { createdAt: 'asc' }],
    });
    return ok({
      services: services.map(serviceDto),
      paypal: { clientId: PAYPAL_CLIENT_ID, env: paypalEnv }, // client id is public by design
    });
  } catch (e) {
    return err(e);
  }
}
