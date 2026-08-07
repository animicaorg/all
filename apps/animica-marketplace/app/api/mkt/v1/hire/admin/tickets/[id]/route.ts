import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanStr } from '@/lib/hire';
import { requireHireAdmin } from '@/lib/hireAuth';
import { notifyTicket } from '@/lib/hireMail';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

async function loadTicket(ticketId: string) {
  const t = await prisma.hireTicket.findUnique({
    where: { ticketId },
    include: {
      user: { select: { email: true, name: true } },
      order: { select: { orderId: true, serviceName: true } },
      messages: { orderBy: { createdAt: 'asc' }, take: 200 },
    },
  });
  if (!t) throw new ApiError(404, 'not_found', 'ticket not found');
  return t;
}

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    await requireHireAdmin(req);
    return ok({ ticket: await loadTicket(params.id) });
  } catch (e) {
    return err(e);
  }
}

// Admin ticket actions: reply | close | reopen. Replies email the customer.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    await requireHireAdmin(req);
    const t = await loadTicket(params.id);
    let body: any = {};
    try { body = await req.json(); } catch {}
    const action = String(body?.action || 'reply');

    if (action === 'close' || action === 'reopen') {
      const ticket = await prisma.hireTicket.update({
        where: { id: t.id },
        data: { status: action === 'close' ? 'closed' : 'open' },
        include: {
          user: { select: { email: true, name: true } },
          order: { select: { orderId: true, serviceName: true } },
          messages: { orderBy: { createdAt: 'asc' } },
        },
      });
      return ok({ ticket });
    }

    const message = cleanStr(body?.message, 8000, 'message', true)!;
    const ticket = await prisma.hireTicket.update({
      where: { id: t.id },
      data: { status: 'answered', messages: { create: { author: 'admin', body: message } } },
      include: {
        user: { select: { email: true, name: true } },
        order: { select: { orderId: true, serviceName: true } },
        messages: { orderBy: { createdAt: 'asc' } },
      },
    });

    notifyTicket({
      toAdmin: false,
      ticketId: t.ticketId,
      subject: t.subject,
      body: message,
      customerEmail: t.user.email,
      customerName: t.user.name,
      orderRef: t.order?.orderId ?? null,
    }).catch(() => {});

    return ok({ ticket });
  } catch (e) {
    return err(e);
  }
}
