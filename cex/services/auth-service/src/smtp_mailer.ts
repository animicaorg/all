import net from "node:net";
import tls from "node:tls";
import { randomUUID } from "node:crypto";

export type SmtpConfig = {
  host?: string;
  port: number;
  secure: boolean;
  user?: string;
  password?: string;
  from: string;
};

export type EmailMessage = {
  to: string;
  subject: string;
  html: string;
  text: string;
};

type SmtpSocket = net.Socket | tls.TLSSocket;

function sanitizeHeader(value: string): string {
  return value.replace(/[\r\n]+/g, " ").trim();
}

function parseMailbox(value: string): { name?: string; email: string } {
  const sanitized = sanitizeHeader(value);
  const angleMatch = sanitized.match(/^(.*?)<([^<>]+)>$/);
  if (angleMatch) {
    const name = angleMatch[1]?.replace(/^"|"$/g, "").trim();
    return {
      name: name || undefined,
      email: angleMatch[2].trim(),
    };
  }
  return { email: sanitized };
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function address(value: string): string {
  return `<${parseMailbox(value).email}>`;
}

function dotStuff(value: string): string {
  return value.replace(/\r?\n/g, "\r\n").replace(/^\./gm, "..");
}

function createMimeMessage(config: SmtpConfig, message: EmailMessage): string {
  const boundary = `animica-${randomUUID()}`;
  const from = parseMailbox(config.from);
  const to = parseMailbox(message.to);
  const subject = sanitizeHeader(message.subject);
  const fromHeader = `${from.name || "Animica Exchange"} <${from.email}>`;

  return [
    `From: ${fromHeader}`,
    `To: ${to.email}`,
    `Subject: ${subject}`,
    `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${randomUUID()}@trade.animica.org>`,
    "MIME-Version: 1.0",
    `Content-Type: multipart/alternative; boundary="${boundary}"`,
    "",
    `--${boundary}`,
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: 7bit",
    "",
    message.text,
    "",
    `--${boundary}`,
    "Content-Type: text/html; charset=UTF-8",
    "Content-Transfer-Encoding: 7bit",
    "",
    message.html,
    "",
    `--${boundary}--`,
    "",
  ].join("\r\n");
}

function parseCode(response: string): number {
  const match = response.match(/^(\d{3})/m);
  return match ? Number(match[1]) : 0;
}

function expectCode(response: string, expected: number | number[], command: string) {
  const code = parseCode(response);
  const expectedCodes = Array.isArray(expected) ? expected : [expected];
  if (!expectedCodes.includes(code)) {
    throw new Error(`SMTP ${command} failed with ${code || "unknown"}: ${response.trim()}`);
  }
}

class SmtpSession {
  private buffer = "";
  private waiting:
    | {
        resolve: (value: string) => void;
        reject: (error: Error) => void;
      }
    | null = null;

  constructor(private socket: SmtpSocket) {
    this.socket.setEncoding("utf8");
    this.socket.on("data", (chunk) => this.onData(String(chunk)));
    this.socket.on("error", (error) => this.rejectWaiting(error));
    this.socket.on("end", () => this.rejectWaiting(new Error("SMTP connection ended")));
  }

  private onData(chunk: string) {
    this.buffer += chunk;
    if (!this.waiting) return;

    const lines = this.buffer.split(/\r?\n/).filter(Boolean);
    if (lines.length === 0) return;
    const lastLine = lines[lines.length - 1];
    if (/^\d{3} /.test(lastLine)) {
      const response = this.buffer;
      this.buffer = "";
      const waiting = this.waiting;
      this.waiting = null;
      waiting.resolve(response);
    }
  }

  private rejectWaiting(error: Error) {
    if (!this.waiting) return;
    const waiting = this.waiting;
    this.waiting = null;
    waiting.reject(error);
  }

  readResponse(): Promise<string> {
    return new Promise((resolve, reject) => {
      this.waiting = { resolve, reject };
      this.onData("");
    });
  }

  async command(command: string, expected: number | number[]) {
    this.socket.write(`${command}\r\n`);
    const response = await this.readResponse();
    expectCode(response, expected, command.split(/\s+/, 1)[0] || "command");
    return response;
  }

  async data(message: string) {
    await this.command("DATA", 354);
    this.socket.write(`${dotStuff(message)}\r\n.\r\n`);
    const response = await this.readResponse();
    expectCode(response, 250, "DATA body");
  }

  async startTls(host: string): Promise<SmtpSession> {
    await this.command("STARTTLS", 220);
    const tlsSocket = tls.connect({ socket: this.socket as net.Socket, servername: host });
    await new Promise<void>((resolve, reject) => {
      tlsSocket.once("secureConnect", resolve);
      tlsSocket.once("error", reject);
    });
    return new SmtpSession(tlsSocket);
  }

  close() {
    this.socket.end();
  }
}

async function connect(config: SmtpConfig): Promise<SmtpSession> {
  const socket = config.secure
    ? tls.connect({ host: config.host, port: config.port, servername: config.host })
    : net.connect({ host: config.host, port: config.port });

  await new Promise<void>((resolve, reject) => {
    socket.once(config.secure ? "secureConnect" : "connect", resolve);
    socket.once("error", reject);
  });

  const session = new SmtpSession(socket);
  const greeting = await session.readResponse();
  expectCode(greeting, 220, "greeting");
  return session;
}

export function isSmtpConfigured(config: SmtpConfig): boolean {
  return Boolean(config.host && config.from);
}

export function renderVerificationEmail(params: {
  fullName?: string | null;
  verificationUrl: string;
  expiresHours: number;
}) {
  const displayName = params.fullName?.trim() || "Animica trader";
  const safeName = escapeHtml(displayName);
  const safeUrl = escapeHtml(params.verificationUrl);
  const expiryText = `${params.expiresHours} hour${params.expiresHours === 1 ? "" : "s"}`;

  const text = [
    `Hi ${displayName},`,
    "",
    "Verify your email address to activate your Animica Exchange account.",
    `Verification link: ${params.verificationUrl}`,
    "",
    `This link expires in ${expiryText}. If you did not create an Animica Exchange account, ignore this email.`,
    "",
    "Animica Exchange",
  ].join("\n");

  const html = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Verify your Animica Exchange email</title>
  </head>
  <body style="margin:0;background:#020617;color:#e2e8f0;font-family:Inter,Segoe UI,Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#020617;padding:32px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:620px;background:#0f172a;border:1px solid #263449;border-radius:18px;overflow:hidden;">
            <tr>
              <td style="padding:32px 32px 18px;background:#111827;">
                <div style="font-size:13px;letter-spacing:0.16em;text-transform:uppercase;color:#38bdf8;font-weight:700;">Animica Exchange</div>
                <h1 style="margin:18px 0 8px;font-size:30px;line-height:1.15;color:#ffffff;">Verify your email</h1>
                <p style="margin:0;color:#cbd5e1;font-size:16px;line-height:1.6;">Hi ${safeName}, confirm this address to activate your account and unlock exchange access.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:30px 32px;">
                <table role="presentation" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="border-radius:12px;background:#2563eb;">
                      <a href="${safeUrl}" style="display:inline-block;padding:14px 22px;color:#ffffff;text-decoration:none;font-weight:700;font-size:15px;">Verify email address</a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;color:#cbd5e1;font-size:14px;line-height:1.65;">This verification link expires in ${expiryText}. Until verification is complete, login and exchange actions are locked.</p>
                <p style="margin:18px 0 0;color:#94a3b8;font-size:13px;line-height:1.65;">If the button does not open, paste this URL into your browser:</p>
                <p style="margin:8px 0 0;word-break:break-all;color:#7dd3fc;font-size:13px;line-height:1.5;">${safeUrl}</p>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 32px 30px;border-top:1px solid #263449;color:#94a3b8;font-size:12px;line-height:1.6;">
                If you did not create an Animica Exchange account, you can ignore this email.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>`;

  return { text, html };
}

export async function sendSmtpMail(config: SmtpConfig, message: EmailMessage): Promise<void> {
  if (!isSmtpConfigured(config)) {
    throw new Error("SMTP is not configured");
  }

  let session = await connect(config);
  try {
    const ehlo = await session.command("EHLO trade.animica.org", 250);
    if (!config.secure && /STARTTLS/im.test(ehlo)) {
      session = await session.startTls(config.host!);
      await session.command("EHLO trade.animica.org", 250);
    }

    if (config.user && config.password) {
      const auth = Buffer.from(`\u0000${config.user}\u0000${config.password}`).toString("base64");
      await session.command(`AUTH PLAIN ${auth}`, 235);
    }

    await session.command(`MAIL FROM:${address(config.from)}`, 250);
    await session.command(`RCPT TO:${address(message.to)}`, [250, 251]);
    await session.data(createMimeMessage(config, message));
    await session.command("QUIT", 221).catch(() => undefined);
  } finally {
    session.close();
  }
}
