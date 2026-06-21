/**
 * Magic-link mailer. Inactive until SMTP creds are present in studio-host/.env
 * (SMTP_USER / SMTP_PASS, e.g. the animicaorg@gmail.com app password). Until
 * then mailerReady() is false and the UI hides the magic-link option.
 */
import nodemailer from 'nodemailer';

let transport = null;

export function mailerReady() {
  return Boolean(process.env.SMTP_USER && process.env.SMTP_PASS);
}

function getTransport() {
  if (transport) return transport;
  transport = nodemailer.createTransport({
    host: process.env.SMTP_HOST || 'smtp.gmail.com',
    port: parseInt(process.env.SMTP_PORT || '465', 10),
    secure: (process.env.SMTP_SECURE || '1') !== '0',
    auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS },
  });
  return transport;
}

export async function sendMagicLink(to, link) {
  if (!mailerReady()) throw new Error('mailer not configured');
  const from = process.env.EMAIL_FROM || process.env.SMTP_USER;
  await getTransport().sendMail({
    from: `Animica Studio <${from}>`,
    to,
    subject: 'Your Animica Studio sign-in link',
    text: `Sign in to Animica Studio:\n\n${link}\n\nThis link expires in 15 minutes. If you didn't request it, ignore this email.`,
    html: `<p>Sign in to <b>Animica Studio</b>:</p>
<p><a href="${link}" style="display:inline-block;padding:12px 20px;background:#5b6cf0;color:#fff;border-radius:10px;text-decoration:none;font-family:sans-serif">Open Animica Studio</a></p>
<p style="color:#667;font-size:13px">This link expires in 15 minutes. If you didn't request it, ignore this email.</p>`,
  });
}
