import { randomBytes } from 'node:crypto';
import { ApiError } from './api';

// Shared helpers for the Hire Me managed-services feature (/hire, dashboard.animica.dev,
// admin.animica.dev). Money here is whole USD integers (PayPal side), NOT nANM.

export const HIRE_NOTIFY_EMAIL = process.env.HIRE_NOTIFY_EMAIL || 'animicaorg@gmail.com';
export const HIRE_SUPPORT_EMAIL = HIRE_NOTIFY_EMAIL;

export const PAYMENT_STATUSES = ['draft', 'pending', 'active', 'suspended', 'cancelled', 'failed', 'quote'] as const;
export const DEPLOYMENT_STATUSES = [
  'awaiting_payment', 'pending_setup', 'active', 'suspended', 'cancelled', 'quote_requested',
] as const;

// Human-facing ids: Crockford-ish base32 without confusable chars (0/O, 1/I/L).
const ID_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ';
function humanId(prefix: string, len = 8): string {
  const bytes = randomBytes(len);
  let out = '';
  for (let i = 0; i < len; i++) out += ID_ALPHABET[bytes[i] % ID_ALPHABET.length];
  return `${prefix}-${out}`;
}
export const newOrderId = () => humanId('HM');
export const newTicketId = () => humanId('HT');

// ── input validation ─────────────────────────────────────────────────────────
export function cleanStr(v: unknown, max: number, field: string, required = false): string | null {
  if (v == null || v === '') {
    if (required) throw new ApiError(400, 'invalid', `${field} is required`);
    return null;
  }
  if (typeof v !== 'string') throw new ApiError(400, 'invalid', `${field} must be a string`);
  const t = v.trim();
  if (required && !t) throw new ApiError(400, 'invalid', `${field} is required`);
  if (t.length > max) throw new ApiError(400, 'invalid', `${field} too long (max ${max})`);
  return t || null;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
export function cleanEmail(v: unknown): string {
  const e = cleanStr(v, 254, 'email', true)!.toLowerCase();
  if (!EMAIL_RE.test(e)) throw new ApiError(400, 'invalid', 'invalid email address');
  return e;
}

export function cleanPassword(v: unknown): string {
  if (typeof v !== 'string') throw new ApiError(400, 'invalid', 'password must be a string');
  if (v.length < 8) throw new ApiError(400, 'invalid', 'password must be at least 8 characters');
  if (v.length > 200) throw new ApiError(400, 'invalid', 'password too long');
  return v;
}

// ── public DTOs (never leak internals like paypalPlanId to the catalog) ─────
export function serviceDto(s: {
  id: string; slug: string; name: string; tagline: string; description: string;
  features: string[]; icon: string; kind: string; priceUsdMonthly: number | null;
  setupFeeUsd: number; paypalPlanId: string | null; sortOrder: number; active: boolean;
}) {
  return {
    id: s.id,
    slug: s.slug,
    name: s.name,
    tagline: s.tagline,
    description: s.description,
    features: s.features,
    icon: s.icon,
    kind: s.kind,
    priceUsdMonthly: s.priceUsdMonthly,
    setupFeeUsd: s.setupFeeUsd,
    // The catalog only says whether checkout is ready, never the plan id itself.
    checkoutReady: s.kind === 'quote' || !!s.paypalPlanId,
  };
}

export function orderCustomerDto(o: any) {
  return {
    orderId: o.orderId,
    serviceType: o.serviceType,
    serviceName: o.serviceName,
    monthlyPriceUsd: o.monthlyPriceUsd,
    setupFeeUsd: o.setupFeeUsd,
    customerName: o.customerName,
    email: o.email,
    company: o.company,
    domain: o.domain,
    discordUsername: o.discordUsername,
    notes: o.notes,
    paymentStatus: o.paymentStatus,
    deploymentStatus: o.deploymentStatus,
    paypalSubscriptionId: o.paypalSubscriptionId,
    createdAt: o.createdAt,
    updatedAt: o.updatedAt,
  };
}

export function ticketDto(t: any, withMessages = false) {
  return {
    ticketId: t.ticketId,
    subject: t.subject,
    status: t.status,
    priority: t.priority,
    orderRef: t.order?.orderId ?? null,
    createdAt: t.createdAt,
    updatedAt: t.updatedAt,
    ...(withMessages
      ? { messages: (t.messages ?? []).map((m: any) => ({ author: m.author, body: m.body, createdAt: m.createdAt })) }
      : { messageCount: t._count?.messages ?? undefined }),
  };
}
