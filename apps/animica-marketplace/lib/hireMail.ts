import nodemailer from 'nodemailer';
import { HIRE_NOTIFY_EMAIL, HIRE_SUPPORT_EMAIL } from './hire';

// Direct transactional mail for the Hire Me flow (order + ticket notifications). Unlike the
// growth/newsletter path (filesystem outbox, gated bulk sender), these are one-off payment and
// support receipts that must go out immediately — so this module talks SMTP directly, and every
// send is FAIL-SOFT: a mail problem must never break checkout or ticketing.

const SMTP_HOST = process.env.SMTP_HOST || '';
const SMTP_PORT = Number(process.env.SMTP_PORT || 587);
const SMTP_USER = process.env.SMTP_USER || '';
const SMTP_PASS = process.env.SMTP_PASS || '';
const MAIL_FROM = process.env.MAIL_FROM || `Animica <${SMTP_USER || 'no-reply@animica.dev'}>`;

let transport: nodemailer.Transporter | null = null;
function getTransport(): nodemailer.Transporter | null {
  if (!SMTP_HOST) return null;
  if (!transport) {
    transport = nodemailer.createTransport({
      host: SMTP_HOST,
      port: SMTP_PORT,
      secure: SMTP_PORT === 465,
      auth: SMTP_USER ? { user: SMTP_USER, pass: SMTP_PASS } : undefined,
    });
  }
  return transport;
}

export async function sendMailSafe(opts: {
  to: string;
  subject: string;
  text: string;
  html?: string;
  replyTo?: string;
}): Promise<boolean> {
  const t = getTransport();
  if (!t) {
    console.warn('[hire:mail] SMTP not configured — skipped:', opts.subject, '→', opts.to);
    return false;
  }
  try {
    const info = await t.sendMail({
      from: MAIL_FROM,
      to: opts.to,
      subject: opts.subject,
      text: opts.text,
      html: opts.html,
      replyTo: opts.replyTo,
      headers: { 'Auto-Submitted': 'auto-generated' },
    });
    console.log(`[hire:mail] sent to=${opts.to} subject="${opts.subject}" id=${info.messageId}`);
    return true;
  } catch (e) {
    console.error('[hire:mail] send failed:', (e as Error).message);
    return false;
  }
}

// ── templates ────────────────────────────────────────────────────────────────
const esc = (s: unknown) =>
  String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function shell(title: string, bodyHtml: string): string {
  return (
    `<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:560px;margin:0 auto;color:#1c1e26">` +
    `<h2 style="letter-spacing:-0.02em">${esc(title)}</h2>` +
    bodyHtml +
    `<p style="color:#8a8fa3;font-size:12px;margin-top:28px">Animica · <a href="https://animica.dev/hire">animica.dev/hire</a> · ` +
    `support: <a href="mailto:${esc(HIRE_SUPPORT_EMAIL)}">${esc(HIRE_SUPPORT_EMAIL)}</a></p></div>`
  );
}

function factRows(facts: Array<[string, unknown]>): { text: string; html: string } {
  const present = facts.filter(([, v]) => v != null && v !== '');
  const text = present.map(([k, v]) => `${k}: ${v}`).join('\n');
  const html =
    `<table style="border-collapse:collapse;font-size:14px">` +
    present
      .map(
        ([k, v]) =>
          `<tr><td style="padding:3px 14px 3px 0;color:#5a5f73;white-space:nowrap;vertical-align:top">${esc(k)}</td>` +
          `<td style="padding:3px 0">${esc(v)}</td></tr>`,
      )
      .join('') +
    `</table>`;
  return { text, html };
}

export interface OrderMailData {
  orderId: string;
  serviceName: string;
  monthlyPriceUsd: number | null;
  setupFeeUsd: number;
  customerName: string;
  email: string;
  company?: string | null;
  domain?: string | null;
  discordUsername?: string | null;
  notes?: string | null;
  paypalSubscriptionId?: string | null;
  paypalTransactionId?: string | null;
  customerIp?: string | null;
  userAgent?: string | null;
  createdAt: Date | string;
}

export function notifyAdminNewOrder(o: OrderMailData, kind: 'order' | 'quote') {
  const price = o.monthlyPriceUsd != null ? `$${o.monthlyPriceUsd}/month` : 'quote requested';
  const facts = factRows([
    ['Order ID', o.orderId],
    ['Timestamp', new Date(o.createdAt).toISOString()],
    ['Customer Name', o.customerName],
    ['Customer Email', o.email],
    ['Company', o.company],
    ['Selected Service', o.serviceName],
    ['Monthly Price', price],
    ['Setup Fee', o.setupFeeUsd ? `$${o.setupFeeUsd} (one-time)` : undefined],
    ['Domain', o.domain],
    ['Discord Username', o.discordUsername],
    ['Additional Notes', o.notes],
    ['PayPal Transaction ID', o.paypalTransactionId],
    ['Subscription ID', o.paypalSubscriptionId],
    ['Customer IP', o.customerIp],
    ['User Agent', o.userAgent],
  ]);
  const subject = kind === 'quote' ? 'New Hire Me Quote Request' : 'New Hire Me Service Order';
  return sendMailSafe({
    to: HIRE_NOTIFY_EMAIL,
    subject,
    replyTo: o.email,
    text: `${subject}\n\n${facts.text}\n\nAdmin: https://animica.dev/admin/hire`,
    html: shell(subject, facts.html + `<p><a href="https://animica.dev/admin/hire">Open the admin dashboard →</a></p>`),
  });
}

export function confirmCustomerOrder(o: OrderMailData) {
  const dueToday = (o.setupFeeUsd || 0) + (o.monthlyPriceUsd || 0);
  const facts = factRows([
    ['Order ID', o.orderId],
    ['Service', o.serviceName],
    ['Monthly amount', o.monthlyPriceUsd != null ? `$${o.monthlyPriceUsd} USD` : undefined],
    ['One-time setup fee', o.setupFeeUsd ? `$${o.setupFeeUsd} USD` : undefined],
    ['First charge', o.setupFeeUsd ? `$${dueToday} USD (setup + first month)` : undefined],
    ['Billing frequency', 'Monthly, auto-renewing via PayPal'],
    ['Expected response time', 'We start reviewing within 24 hours'],
  ]);
  return sendMailSafe({
    to: o.email,
    subject: `Your Animica order ${o.orderId} — received`,
    text:
      `Thank you! Your order has been received.\n\n${facts.text}\n\n` +
      `Track status and open support tickets on your dashboard:\nhttps://dashboard.animica.dev (or https://animica.dev/dashboard)\n\n` +
      `Questions? Just reply to this email.`,
    html: shell(
      'Thank you! Your order has been received.',
      facts.html +
        `<p>We’ll begin reviewing your deployment shortly. Track status and open support tickets on ` +
        `<a href="https://animica.dev/dashboard">your dashboard</a>.</p>`,
    ),
  });
}

export function confirmCustomerQuote(o: OrderMailData) {
  const facts = factRows([
    ['Request ID', o.orderId],
    ['Service', o.serviceName],
    ['Expected response time', 'We reply within 24 hours with a quote'],
  ]);
  return sendMailSafe({
    to: o.email,
    subject: `Your Animica quote request ${o.orderId} — received`,
    text: `Thanks — we received your request.\n\n${facts.text}\n\nWe'll email you a quote shortly. You can also track it on https://animica.dev/dashboard`,
    html: shell(
      'Quote request received',
      facts.html + `<p>We’ll email you a quote shortly. You can also track it on <a href="https://animica.dev/dashboard">your dashboard</a>.</p>`,
    ),
  });
}

export function notifyTicket(opts: {
  toAdmin: boolean;
  ticketId: string;
  subject: string;
  body: string;
  customerEmail: string;
  customerName?: string | null;
  orderRef?: string | null;
}) {
  const facts = factRows([
    ['Ticket', opts.ticketId],
    ['From', opts.toAdmin ? `${opts.customerName || ''} <${opts.customerEmail}>` : 'Animica support'],
    ['Related order', opts.orderRef],
  ]);
  const bodyBlock = `<pre style="white-space:pre-wrap;font-family:inherit;background:#f4f5f9;padding:12px;border-radius:8px">${esc(opts.body)}</pre>`;
  if (opts.toAdmin) {
    return sendMailSafe({
      to: HIRE_NOTIFY_EMAIL,
      subject: `[${opts.ticketId}] ${opts.subject}`,
      replyTo: opts.customerEmail,
      text: `${facts.text}\n\n${opts.body}\n\nReply via https://animica.dev/admin/hire`,
      html: shell(`Support ticket ${opts.ticketId}`, facts.html + bodyBlock + `<p><a href="https://animica.dev/admin/hire">Reply in the admin dashboard →</a></p>`),
    });
  }
  return sendMailSafe({
    to: opts.customerEmail,
    subject: `[${opts.ticketId}] ${opts.subject}`,
    text: `Animica support replied to your ticket ${opts.ticketId}:\n\n${opts.body}\n\nView the full thread: https://animica.dev/dashboard`,
    html: shell(
      `Support replied — ${opts.ticketId}`,
      bodyBlock + `<p><a href="https://animica.dev/dashboard">View the full thread on your dashboard →</a></p>`,
    ),
  });
}
