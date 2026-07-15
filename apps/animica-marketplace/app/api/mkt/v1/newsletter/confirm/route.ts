import { NextRequest, NextResponse } from 'next/server';
import { confirm } from '@/lib/newsletter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for');
  return xff ? xff.split(',')[0].trim() : (req.headers.get('x-real-ip') || 'unknown');
}

function page(title: string, body: string, ok: boolean): NextResponse {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${title} · Animica</title>
<style>body{margin:0;background:#f6f3ec;color:#1c1a17;font-family:system-ui,Segoe UI,Roboto,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center}
.card{max-width:460px;margin:20px;background:#fff;border:1px solid #e6dfd0;border-radius:18px;padding:30px;text-align:center;box-shadow:0 18px 50px -32px rgba(40,30,15,.45)}
h1{font-family:Georgia,serif;font-weight:500;font-size:24px;margin:0 0 10px;color:${ok ? '#3f8f5b' : '#c8613f'}}
p{color:#3a352d;line-height:1.6}a{color:#a94e30}</style></head>
<body><div class="card"><h1>${title}</h1><p>${body}</p>
<p><a href="https://animica.dev">← Back to animica.dev</a></p></div></body></html>`;
  return new NextResponse(html, { status: ok ? 200 : 400, headers: { 'content-type': 'text/html; charset=utf-8' } });
}

// Double-opt-in confirmation (clicked from the confirm email). Single-use, TTL-bounded.
export async function GET(req: NextRequest) {
  const token = req.nextUrl.searchParams.get('token') || '';
  const res = await confirm(token, clientIp(req));
  if (res.ok) {
    return page('You’re subscribed 🎉',
      'Thanks for confirming — you’ll get Animica ecosystem updates. Every email has a one-click unsubscribe, always.', true);
  }
  return page('Link expired or invalid',
    'This confirmation link is invalid, already used, or expired. Please subscribe again at animica.dev to get a fresh link.', false);
}
