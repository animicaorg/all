import { NextRequest, NextResponse } from 'next/server';
import { normalizeEmail, verifyUnsub, unsubscribe } from '@/lib/newsletter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// One-click unsubscribe. GET = a human clicking the footer link; POST = RFC 8058 one-click
// (List-Unsubscribe-Post: List-Unsubscribe=One-Click) that Gmail/Yahoo fire automatically.
// Idempotent and always effective — a valid token suppresses the address even if the row is gone.

async function handle(req: NextRequest): Promise<{ ok: boolean }> {
  const p = req.nextUrl.searchParams;
  const email = normalizeEmail(p.get('e'));
  const sid = p.get('u') || '';
  const token = p.get('t') || '';
  if (!email || !sid || !token) return { ok: false };
  if (!verifyUnsub(sid, email, token)) return { ok: false };
  await unsubscribe(sid, email);
  return { ok: true };
}

export async function POST(req: NextRequest) {
  const r = await handle(req);
  return NextResponse.json({ ok: r.ok }, { status: r.ok ? 200 : 400 });
}

export async function GET(req: NextRequest) {
  const r = await handle(req);
  const ok = r.ok;
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Unsubscribed · Animica</title>
<style>body{margin:0;background:#f6f3ec;color:#1c1a17;font-family:system-ui,Segoe UI,Roboto,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center}
.card{max-width:460px;margin:20px;background:#fff;border:1px solid #e6dfd0;border-radius:18px;padding:30px;text-align:center;box-shadow:0 18px 50px -32px rgba(40,30,15,.45)}
h1{font-family:Georgia,serif;font-weight:500;font-size:24px;margin:0 0 10px}p{color:#3a352d;line-height:1.6}a{color:#a94e30}</style></head>
<body><div class="card"><h1>${ok ? 'Unsubscribed' : 'Invalid link'}</h1>
<p>${ok ? "You’ve been removed from the Animica newsletter and won’t receive further emails. Changed your mind? You can re-subscribe at animica.dev." : 'This unsubscribe link is invalid.'}</p>
<p><a href="https://animica.dev">animica.dev</a></p></div></body></html>`;
  return new NextResponse(html, { status: ok ? 200 : 400, headers: { 'content-type': 'text/html; charset=utf-8' } });
}
