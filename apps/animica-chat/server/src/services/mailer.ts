// Magic-link mailer. Two drivers:
//   - "console" (dev): prints the link to stdout, no SMTP needed.
//   - "smtp" (prod): uses nodemailer with the SMTP_* env config.
//
// Falls through to the console driver when SMTP_HOST is unset even if
// MAIL_DRIVER=smtp; this keeps a half-configured prod from silently
// breaking auth.

import nodemailer, { Transporter } from 'nodemailer';
import { env } from '../env';

let transport: Transporter | null = null;

function getTransport(): Transporter | null {
  if (env.MAIL_DRIVER !== 'smtp' || !env.SMTP_HOST) return null;
  if (transport) return transport;
  transport = nodemailer.createTransport({
    host: env.SMTP_HOST,
    port: env.SMTP_PORT,
    secure: env.SMTP_PORT === 465,
    auth: env.SMTP_USER ? { user: env.SMTP_USER, pass: env.SMTP_PASS } : undefined,
  });
  return transport;
}

export async function sendMagicLinkEmail(opts: {
  to: string;
  link: string;
}): Promise<void> {
  const subject = 'Your Animica Chat sign-in link';
  const text = [
    'Sign in to Animica Chat by clicking the link below.',
    '',
    opts.link,
    '',
    'This link expires in 15 minutes. If you did not request it, ignore this email.',
  ].join('\n');

  const html = `
    <p>Sign in to <strong>Animica Chat</strong> by clicking the button below.</p>
    <p><a href="${opts.link}" style="display:inline-block;background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none">Sign in</a></p>
    <p>This link expires in 15 minutes.</p>
    <p style="color:#64748b;font-size:12px">If you did not request it, ignore this email.</p>
  `;

  const t = getTransport();
  if (!t) {
    // Console driver — print the link so dev flows work without SMTP.
    // eslint-disable-next-line no-console
    console.log(
      `\n[mailer:console] Magic link for ${opts.to}\n  ${opts.link}\n`,
    );
    return;
  }
  const info = await t.sendMail({
    from: env.MAIL_FROM,
    to: opts.to,
    subject,
    text,
    html,
  });
  // Log the message id + Gmail's accept response so journal makes it
  // obvious that the send actually happened (and which provider replied).
  // eslint-disable-next-line no-console
  console.log(
    `[mailer:smtp] sent to=${opts.to} messageId=${info.messageId} response=${(info.response || '').slice(0, 120)}`,
  );
}
