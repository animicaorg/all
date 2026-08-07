import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanStr, newTicketId, ticketDto } from '@/lib/hire';
import { requireCustomer } from '@/lib/hireAuth';
import { notifyTicket } from '@/lib/hireMail';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Customer support tickets: list mine / open a new one.
export async function GET(_req: NextRequest) {
  try {
    const cust = await requireCustomer();
    const tickets = await prisma.hireTicket.findMany({
      where: { userId: cust.id },
      orderBy: { updatedAt: 'desc' },
      include: { order: { select: { orderId: true } }, _count: { select: { messages: true } } },
      take: 100,
    });
    return ok({ tickets: tickets.map((t) => ticketDto(t)) });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const cust = await requireCustomer();
    let body: any = {};
    try { body = await req.json(); } catch {}
    const subject = cleanStr(body?.subject, 200, 'subject', true)!;
    const message = cleanStr(body?.message, 8000, 'message', true)!;
    const priority = ['low', 'normal', 'high'].includes(body?.priority) ? body.priority : 'normal';
    const orderRef = cleanStr(body?.orderRef, 40, 'orderRef');

    // Flood guard: max 10 new tickets per customer per day.
    const dayAgo = new Date(Date.now() - 24 * 3600 * 1000);
    const recent = await prisma.hireTicket.count({ where: { userId: cust.id, createdAt: { gte: dayAgo } } });
    if (recent >= 10) throw new ApiError(429, 'rate_limited', 'too many tickets today — please add to an existing ticket');

    let orderId: string | null = null;
    if (orderRef) {
      const order = await prisma.servicesOrder.findUnique({ where: { orderId: orderRef } });
      if (!order || order.userId !== cust.id) throw new ApiError(400, 'invalid', 'unknown order reference');
      orderId = order.id;
    }

    const ticket = await prisma.hireTicket.create({
      data: {
        ticketId: newTicketId(),
        userId: cust.id,
        orderId,
        subject,
        priority,
        messages: { create: { author: 'customer', body: message } },
      },
      include: { order: { select: { orderId: true } }, messages: true },
    });

    notifyTicket({
      toAdmin: true,
      ticketId: ticket.ticketId,
      subject,
      body: message,
      customerEmail: cust.email,
      customerName: cust.name,
      orderRef: ticket.order?.orderId ?? null,
    }).catch(() => {});

    return ok({ ticket: ticketDto(ticket, true) });
  } catch (e) {
    return err(e);
  }
}
