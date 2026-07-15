import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { homedir } from 'node:os';

// The web app NEVER opens an SMTP connection. It enqueues transactional mail (the double-opt-in
// confirm email) to a filesystem outbox; the single gated Python sender (animica growth) is the
// only thing that talks to SMTP — one compliant send path, dry-run by default. This keeps the web
// app dependency-free and keeps all deliverability/rate/compliance logic in one place.

const OUTBOX = process.env.GROWTH_OUTBOX_DIR || join(homedir(), '.animica', 'growth', 'outbox');

export interface OutboundMail {
  kind: 'transactional' | 'campaign';
  to: string;
  subject: string;
  html: string;
  text: string;
  headers?: Record<string, string>;
  enqueuedAt: string;
}

export function enqueueMail(mail: Omit<OutboundMail, 'enqueuedAt'>): { queued: boolean; id: string } {
  try {
    mkdirSync(OUTBOX, { recursive: true });
    const id = Date.now().toString(36) + '-' + randomBytes(6).toString('hex');
    const env: OutboundMail = { ...mail, enqueuedAt: new Date().toISOString() };
    writeFileSync(join(OUTBOX, id + '.json'), JSON.stringify(env), { mode: 0o600 });
    return { queued: true, id };
  } catch {
    return { queued: false, id: '' };
  }
}

// Transactional confirm email for double opt-in. Purely transactional (a single confirm CTA, no
// promotional payload) so it stays CAN-SPAM-exempt.
export function confirmEmail(to: string, confirmUrl: string): Omit<OutboundMail, 'enqueuedAt'> {
  const text =
    `Confirm your Animica newsletter subscription\n\n` +
    `You (or someone using this address) asked to receive the Animica newsletter.\n` +
    `Confirm to start receiving it:\n\n${confirmUrl}\n\n` +
    `If you didn't request this, just ignore this email — you won't be subscribed.\n\n` +
    `Animica · https://animica.dev`;
  const html =
    `<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;color:#1c1a17">` +
    `<h2 style="font-family:Georgia,serif">Confirm your Animica newsletter subscription</h2>` +
    `<p>You (or someone using this address) asked to receive the Animica newsletter.</p>` +
    `<p><a href="${confirmUrl}" style="display:inline-block;background:#c8613f;color:#fff;padding:11px 20px;border-radius:10px;text-decoration:none;font-weight:600">Confirm subscription</a></p>` +
    `<p style="color:#6f685c;font-size:13px">If you didn't request this, ignore this email — you won't be subscribed. ` +
    `The link expires in 72 hours.</p>` +
    `<p style="color:#938b7c;font-size:12px">Animica · <a href="https://animica.dev">animica.dev</a></p></div>`;
  return {
    kind: 'transactional',
    to,
    subject: 'Confirm your Animica newsletter subscription',
    html,
    text,
    headers: { 'Auto-Submitted': 'auto-generated' },
  };
}
