// Magic-link mailer. Two drivers:
//   - "console" (dev): prints the link to stdout, no SMTP needed.
//   - "smtp" (prod): nodemailer with the SMTP_* env config (Gmail SMTP,
//     from animicaorg@gmail.com).
//
// Falls through to the console driver when SMTP_HOST is unset even if
// MAIL_DRIVER=smtp, so a half-configured prod doesn't silently break auth.
// Ported from apps/animica-chat/server/src/services/mailer.ts.

import nodemailer, { type Transporter } from "nodemailer";
import { env } from "./env";

let transport: Transporter | null = null;

function getTransport(): Transporter | null {
  const e = env();
  if (e.MAIL_DRIVER !== "smtp" || !e.SMTP_HOST) return null;
  if (transport) return transport;
  transport = nodemailer.createTransport({
    host: e.SMTP_HOST,
    port: e.SMTP_PORT,
    secure: e.SMTP_PORT === 465,
    auth: e.SMTP_USER ? { user: e.SMTP_USER, pass: e.SMTP_PASS } : undefined,
  });
  return transport;
}

export async function sendMagicLinkEmail(opts: {
  to: string;
  link: string;
}): Promise<void> {
  const subject = "Your Animica Rig Rental sign-in link";
  const text = [
    "Sign in to Animica Rig Rental by clicking the link below.",
    "",
    opts.link,
    "",
    "This link expires in 15 minutes. If you did not request it, ignore this email.",
  ].join("\n");

  const html = `
    <p>Sign in to <strong>Animica Rig Rental</strong> by clicking the button below.</p>
    <p><a href="${opts.link}" style="display:inline-block;background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none">Sign in</a></p>
    <p>This link expires in 15 minutes.</p>
    <p style="color:#64748b;font-size:12px">If you did not request it, ignore this email.</p>
  `;

  const t = getTransport();
  if (!t) {
    // eslint-disable-next-line no-console
    console.log(`\n[mailer:console] Magic link for ${opts.to}\n  ${opts.link}\n`);
    return;
  }
  const info = await t.sendMail({ from: env().MAIL_FROM, to: opts.to, subject, text, html });
  // eslint-disable-next-line no-console
  console.log(
    `[mailer:smtp] sent to=${opts.to} messageId=${info.messageId} response=${(info.response || "").slice(0, 120)}`,
  );
}
