import type { APIRoute } from "astro";
import { promises as fs } from "node:fs";
import path from "node:path";

/**
 * POST /api/newsletter
 *
 * Minimal email capture with two backends:
 *  1) Webhook (preferred): set NEWSLETTER_WEBHOOK_URL to forward submissions.
 *  2) File drop (fallback): appends a CSV line under ./.data/newsletter/subscribers.csv
 *
 * Accepted payloads:
 *  - JSON: { email: string, name?: string, website?: string }  // "website" is a honeypot: must be empty
 *  - Form: application/x-www-form-urlencoded with the same fields
 */

const WEBHOOK = process.env.NEWSLETTER_WEBHOOK_URL;
const DROP_DIR = process.env.NEWSLETTER_DROP_DIR || path.join(process.cwd(), ".data", "newsletter");
const DROP_FILE = path.join(DROP_DIR, "subscribers.csv");

// naive in-process dedupe (helps on retries; not robust across serverless instances)
const recent = new Map<string, number>();
const DEDUPE_MS = 5 * 60 * 1000; // 5 minutes

function validateEmail(email: unknown): email is string {
  if (typeof email !== "string") return false;
  const e = email.trim();
  // Reasonable but permissive RFC 5322-ish check
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(e) && e.length <= 254;
}

async function parseBody(request: Request): Promise<{ email: string; name?: string; website?: string }> {
  const ctype = request.headers.get("content-type") || "";
  if (ctype.includes("application/json")) {
    const j = (await request.json()) as any;
    return { email: j?.email, name: j?.name, website: j?.website };
  }
  if (ctype.includes("application/x-www-form-urlencoded")) {
    const text = await request.text();
    const p = new URLSearchParams(text);
    return { email: p.get("email") || "", name: p.get("name") || undefined, website: p.get("website") || undefined };
  }
  // try query params as last resort
  const url = new URL(request.url);
  return { email: url.searchParams.get("email") || "", name: url.searchParams.get("name") || undefined, website: url.searchParams.get("website") || undefined };
}

function getClientHints(req: Request) {
  const ipHeader = (req.headers.get("x-forwarded-for") || req.headers.get("x-real-ip") || "").split(",")[0].trim();
  const ip = ipHeader || undefined;
  const ua = req.headers.get("user-agent") || undefined;
  const referer = req.headers.get("referer") || undefined;
  return { ip, ua, referer };
}

async function ensureDropFile() {
  await fs.mkdir(DROP_DIR, { recursive: true });
  try {
    await fs.access(DROP_FILE);
  } catch {
    const header = 'timestamp_iso,email,name,ip,user_agent,referer\n';
    await fs.appendFile(DROP_FILE, header, { encoding: "utf8" });
  }
}

function csvEscape(s: string | undefined): string {
  if (!s) return "";
  const q = String(s).replace(/"/g, '""');
  return `"${q}"`;
}

async function fileDrop(entry: {
  timestamp: string;
  email: string;
  name?: string;
  ip?: string;
  ua?: string;
  referer?: string;
}) {
  await ensureDropFile();
  const line = [
    csvEscape(entry.timestamp),
    csvEscape(entry.email),
    csvEscape(entry.name),
    csvEscape(entry.ip),
    csvEscape(entry.ua),
    csvEscape(entry.referer),
  ].join(",") + "\n";
  await fs.appendFile(DROP_FILE, line, { encoding: "utf8" });
}

async function postWebhook(url: string, payload: unknown, signal?: AbortSignal) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`Webhook HTTP ${res.status}: ${txt.slice(0, 200)}`);
  }
}

export const POST: APIRoute = async ({ request }) => {
  try {
    const { email, name, website } = await parseBody(request);
    const { ip, ua, referer } = getClientHints(request);

    // Honeypot: bots often fill this
    if (website && website.trim().length > 0) {
      return new Response(JSON.stringify({ ok: true, skipped: true }), {
        status: 200,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }

    if (!validateEmail(email)) {
      return new Response(JSON.stringify({ ok: false, error: "Invalid email" }), {
        status: 400,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }

    // Simple dedupe to reduce duplicate rows on fast retries
    const key = `${email.toLowerCase()}`;
    const now = Date.now();
    const last = recent.get(key) || 0;
    if (now - last < DEDUPE_MS) {
      return new Response(JSON.stringify({ ok: true, deduped: true }), {
        status: 200,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }
    recent.set(key, now);

    const timestamp = new Date().toISOString();
    const record = { timestamp, email: email.trim(), name: name?.trim(), ip, ua, referer };

    // Preferred: webhook
    if (WEBHOOK && WEBHOOK.startsWith("http")) {
      await postWebhook(WEBHOOK, record);
    } else {
      // Fallback: drop to CSV file (best-effort; ephemeral on serverless)
      await fileDrop(record);
    }

    return new Response(JSON.stringify({ ok: true, message: "Thanks! Please check your inbox soon." }), {
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  } catch (err: any) {
    return new Response(JSON.stringify({ ok: false, error: err?.message ?? "Unknown error" }), {
      status: 500,
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  }
};

// Optional: answer OPTIONS for simple CORS preflight (same-origin by default)
export const OPTIONS: APIRoute = async () =>
  new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-methods": "POST, OPTIONS",
      "access-control-allow-headers": "content-type",
      "access-control-max-age": "600",
    },
  });
