import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { prisma } from './db';
import { config } from './config';

// Consent-only newsletter backbone (Animica Growth Engine 8.1.0).
//
// Load-bearing invariants (mirrored by animica/growth/* and enforced structurally, not by flags):
//  * An address reaches CONFIRMED ONLY by redeeming an emailed double-opt-in token. There is no
//    admin/import path to CONFIRMED.
//  * Suppression is append-only and always honored; a suppressed address can never re-subscribe.
//  * confirmedRecipients() is the ONLY recipient source for the sender — no caller-supplied list.
//  * Unsubscribe is public, idempotent, and can never fail-open into "still subscribed".

const PEPPER = process.env.GROWTH_CONFIRM_PEPPER || ('confirm:' + config.sessionSecret);
const UNSUB_SECRET = process.env.GROWTH_UNSUB_SECRET || ('unsub:' + config.sessionSecret);
const CONFIRM_TTL_HOURS = Number(process.env.GROWTH_CONFIRM_TTL_HOURS ?? 72);

export const CONSENT_TEXT =
  'I agree to receive the Animica email newsletter (product updates, ecosystem news). ' +
  'I can unsubscribe at any time via the one-click link in every email.';

// RFC5322-ish validation + normalization. Rejects control chars / CRLF (header-injection guard).
const EMAIL_RE = /^[^\s@"'<>()\[\]\\,;:]+@[^\s@"'<>()\[\]\\,;:]+\.[^\s@"'<>()\[\]\\,;:]+$/;
export function normalizeEmail(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const e = raw.trim().toLowerCase();
  if (e.length < 3 || e.length > 254) return null;
  if (/[\x00-\x1f\x7f]/.test(e)) return null; // control chars incl. CR/LF
  if (!EMAIL_RE.test(e)) return null;
  return e;
}

export function emailHash(email: string): string {
  return createHash('sha3-256').update(email.toLowerCase()).digest('hex');
}
function confirmHashOf(rawToken: string): string {
  return createHash('sha3-256').update(PEPPER + '|' + rawToken).digest('hex');
}
export function unsubToken(subscriberId: string, email: string): string {
  return createHmac('sha256', UNSUB_SECRET).update(subscriberId + ':' + email.toLowerCase()).digest('base64url');
}
export function verifyUnsub(subscriberId: string, email: string, token: string): boolean {
  const want = unsubToken(subscriberId, email);
  const a = Buffer.from(want); const b = Buffer.from(token || '');
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function isSuppressed(email: string): Promise<boolean> {
  const row = await prisma.newsletterSuppression.findUnique({ where: { emailHash: emailHash(email) } });
  return !!row;
}

// Subscribe (double opt-in). Suppression is checked FIRST → a previously-opted-out address gets a
// neutral no-op (no row created, no email). Returns the RAW confirm token ONLY for a freshly
// (re)issued PENDING signup so the caller can email the confirm link; null means "send nothing".
export async function subscribe(opts: {
  email: string; source?: string; ip?: string; ua?: string;
}): Promise<{ neutral: true; rawToken?: string; subscriberId?: string }> {
  const email = opts.email;
  if (await isSuppressed(email)) return { neutral: true }; // opted-out: silently do nothing

  const existing = await prisma.newsletterSubscriber.findUnique({ where: { email } });
  if (existing && existing.status === 'CONFIRMED') return { neutral: true }; // already in — no re-confirm spam
  if (existing && existing.status === 'UNSUBSCRIBED') return { neutral: true }; // treat like suppression

  // Anti subscription-bombing: don't reissue a confirm within the cooldown.
  const cooldownH = Number(process.env.GROWTH_PENDING_CONFIRM_COOLDOWN_H ?? 24);
  if (existing && existing.confirmSentAt && Date.now() - existing.confirmSentAt.getTime() < cooldownH * 3600_000) {
    return { neutral: true };
  }

  const rawToken = randomBytes(32).toString('base64url');
  const data = {
    email,
    status: 'PENDING' as const,
    confirmHash: confirmHashOf(rawToken),
    confirmSentAt: new Date(),
    confirmExpires: new Date(Date.now() + CONFIRM_TTL_HOURS * 3600_000),
    source: (opts.source ?? '').slice(0, 120),
    consentIp: opts.ip ?? null,
    consentUa: (opts.ua ?? '').slice(0, 300),
    consentText: CONSENT_TEXT,
  };
  const sub = existing
    ? await prisma.newsletterSubscriber.update({ where: { email }, data })
    : await prisma.newsletterSubscriber.create({ data });
  return { neutral: true, rawToken, subscriberId: sub.id };
}

// Confirm: single-use, TTL-bounded. The only PENDING→CONFIRMED transition.
export async function confirm(rawToken: string, ip?: string): Promise<{ ok: boolean; email?: string }> {
  if (!rawToken) return { ok: false };
  const sub = await prisma.newsletterSubscriber.findFirst({ where: { confirmHash: confirmHashOf(rawToken), status: 'PENDING' } });
  if (!sub) return { ok: false };
  if (sub.confirmExpires && sub.confirmExpires.getTime() < Date.now()) return { ok: false };
  if (await isSuppressed(sub.email)) return { ok: false };
  await prisma.newsletterSubscriber.update({
    where: { id: sub.id },
    data: { status: 'CONFIRMED', confirmedAt: new Date(), confirmHash: null, confirmExpires: null, consentIp: ip ?? sub.consentIp },
  });
  return { ok: true, email: sub.email };
}

// Unsubscribe: public, idempotent, always effective. Writes suppression even if the subscriber row
// is missing, so it can never fail-open. Accepts an HMAC token (one-click) OR is called after token
// verification by the route.
export async function unsubscribe(subscriberId: string, email: string): Promise<{ ok: true }> {
  const eh = emailHash(email);
  await prisma.newsletterSuppression.upsert({
    where: { emailHash: eh }, create: { emailHash: eh, reason: 'UNSUBSCRIBE' }, update: {},
  });
  await prisma.newsletterSubscriber.updateMany({
    where: { OR: [{ id: subscriberId }, { email: email.toLowerCase() }] },
    data: { status: 'UNSUBSCRIBED', unsubscribedAt: new Date() },
  }).catch(() => {});
  return { ok: true };
}

// Suppress by reason (bounce/complaint/erasure) — append-only, used by the sender's feedback loop.
export async function suppress(email: string, reason: string): Promise<void> {
  const eh = emailHash(email);
  await prisma.newsletterSuppression.upsert({ where: { emailHash: eh }, create: { emailHash: eh, reason }, update: {} });
  await prisma.newsletterSubscriber.updateMany({ where: { email: email.toLowerCase() }, data: { status: 'UNSUBSCRIBED' } }).catch(() => {});
}

// The ONE recipient source. CONFIRMED subscribers anti-joined against the suppression list.
// No parameters — an arbitrary recipient list is unrepresentable here by design.
export async function confirmedRecipients(): Promise<Array<{ id: string; email: string; unsubToken: string }>> {
  const [subs, sup] = await Promise.all([
    prisma.newsletterSubscriber.findMany({ where: { status: 'CONFIRMED' }, select: { id: true, email: true } }),
    prisma.newsletterSuppression.findMany({ select: { emailHash: true } }),
  ]);
  const supSet = new Set(sup.map((s) => s.emailHash));
  return subs
    .filter((s) => !supSet.has(emailHash(s.email)))
    .map((s) => ({ id: s.id, email: s.email, unsubToken: unsubToken(s.id, s.email) }));
}

export async function stats() {
  const [pending, confirmed, unsub, suppressed] = await Promise.all([
    prisma.newsletterSubscriber.count({ where: { status: 'PENDING' } }),
    prisma.newsletterSubscriber.count({ where: { status: 'CONFIRMED' } }),
    prisma.newsletterSubscriber.count({ where: { status: 'UNSUBSCRIBED' } }),
    prisma.newsletterSuppression.count(),
  ]);
  return { pending, confirmed, unsubscribed: unsub, suppressed };
}
