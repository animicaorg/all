import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanStr, ticketDto } from '@/lib/hire';
import { requireCustomer } from '@/lib/hireAuth';
import { notifyTicket } from '@/lib/hireMail';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

async function ownTicket(custId: string, ticketId: string) {
  const t = await prisma.hireTicket.findUnique({
    where: { ticketId },
    include: {
      order: { select: { orderId: true } },
      messages: { orderBy: { createdAt: 'asc' }, take: 200 },
    },
  });
  if (!t || t.userId !== custId) throw new ApiError(404, 'not_found', 'ticket not found');
  return t;
}

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const cust = await requireCustomer();
    const t = await ownTicket(cust.id, params.id);
    return ok({ ticket: ticketDto(t, true) });
  } catch (e) {
    return err(e);
  }
}

// Reply (or close) my ticket.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const cust = await requireCustomer();
    const t = await ownTicket(cust.id, params.id);
    let body: any = {};
    try { body = await req.json(); } catch {}

    if (body?.action === 'close') {
      const updated = await prisma.hireTicket.update({
        where: { id: t.id },
        data: { status: 'closed' },
        include: { order: { select: { orderId: true } }, messages: { orderBy: { createdAt: 'asc' } } },
      });
      return ok({ ticket: ticketDto(updated, true) });
    }

    const message = cleanStr(body?.message, 8000, 'message', true)!;
    const updated = await prisma.hireTicket.update({
      where: { id: t.id },
      data: {
        status: 'customer_reply',
        messages: { create: { author: 'customer', body: message } },
      },
      include: { order: { select: { orderId: true } }, messages: { orderBy: { createdAt: 'asc' } } },
    });

    notifyTicket({
      toAdmin: true,
      ticketId: t.ticketId,
      subject: `Re: ${t.subject}`,
      body: message,
      customerEmail: cust.email,
      customerName: cust.name,
      orderRef: t.order?.orderId ?? null,
    }).catch(() => {});

    return ok({ ticket: ticketDto(updated, true) });
  } catch (e) {
    return err(e);
  }
}
