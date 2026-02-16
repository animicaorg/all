import nodemailer from "nodemailer";
import { env } from "@/src/server/env";
import { logger } from "@/src/server/logging/logger";

export async function sendMagicLinkEmail(email: string, link: string) {
  const canSend = env.SMTP_HOST && env.SMTP_PORT && env.SMTP_USER && env.SMTP_PASS && env.SMTP_FROM;
  if (!canSend) {
    logger.info({ email, link }, "SMTP is not configured. Magic link emitted in dev mode.");
    return;
  }

  const transport = nodemailer.createTransport({
    host: env.SMTP_HOST,
    port: env.SMTP_PORT,
    secure: env.SMTP_PORT === 465,
    auth: { user: env.SMTP_USER, pass: env.SMTP_PASS }
  });

  await transport.sendMail({
    from: env.SMTP_FROM,
    to: email,
    subject: "Sign in to Animica Studio",
    text: `Click to sign in: ${link}`
  });
}
