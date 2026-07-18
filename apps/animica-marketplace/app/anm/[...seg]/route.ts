import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { normalizeName } from '@/lib/ans';
import { getObject, verifyObject } from '@/lib/storage';
import { config } from '@/lib/config';

export const dynamic = 'force-dynamic';

// The .anm gateway: GET /anm/<name>[/<sub/path>] resolves the name and serves it.
//  - contentCid present -> serve the hosted HTML in a CSP sandbox (opaque origin, cannot touch
//    animica.dev cookies/DOM), tamper-checked against its CID before serving.
//  - endpoint record present -> 302 redirect to the real service, PRESERVING any sub-path + query
//    (so name.anm/foo?bar deep-links land on <endpoint>/foo?bar, not the bare root).
//  - otherwise -> a branded resolver card (records + owner + register CTA).
// Catch-all so the browser extension can forward deep links; humans can also just visit the URL.

function page(title: string, bodyHtml: string, status = 200): NextResponse {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>${title}</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:15px/1.6 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  background:radial-gradient(1200px 600px at 50% -10%,#182135,#0b0e14 60%);color:#e7ecf3;min-height:100vh;
  display:flex;align-items:center;justify-content:center;padding:32px}
.card{max-width:560px;width:100%;background:rgba(20,26,38,.8);border:1px solid #263042;border-radius:18px;
  padding:34px 32px;box-shadow:0 30px 80px -30px #000}
.badge{display:inline-flex;gap:7px;align-items:center;font-size:12px;color:#8ea2c4;border:1px solid #263042;
  border-radius:999px;padding:5px 11px;margin-bottom:18px}
h1{margin:.1em 0 .2em;font-size:30px;letter-spacing:-.03em}
.fqdn{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#7cc4ff}
p{color:#aebdd6;margin:.4em 0}
.rec{background:#0f1420;border:1px solid #222c3c;border-radius:12px;padding:14px 16px;margin-top:16px;
  font-family:ui-monospace,monospace;font-size:13px;color:#cbd7ea;white-space:pre-wrap;word-break:break-word}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
a.btn{display:inline-block;text-decoration:none;background:linear-gradient(120deg,#6ea8fe,#8b7cff);color:#0b0e14;
  font-weight:600;padding:11px 18px;border-radius:11px}
a.ghost{background:transparent;border:1px solid #33405a;color:#cbd7ea}
.mut{color:#6b7a94;font-size:12.5px;margin-top:22px}
.pill{display:inline-block;background:#111827;border:1px solid #26324a;border-radius:999px;padding:3px 10px;font-size:12px;color:#9fb2d4;margin-right:6px}
</style></head><body><div class="card">${bodyHtml}
<div class="mut">Served by the <b>Animica Internet</b> gateway · sovereign <span class="fqdn">.anm</span> namespace</div>
</div></body></html>`;
  return new NextResponse(html, { status, headers: { 'content-type': 'text/html; charset=utf-8' } });
}

export async function GET(req: NextRequest, { params }: { params: { seg: string[] } }) {
  const seg = Array.isArray(params.seg) ? params.seg : [params.seg];
  const name = normalizeName(seg[0] || '');
  // Remaining path segments after the name become a sub-path we forward to the resolved endpoint.
  const subPath = seg.slice(1).map((s) => encodeURIComponent(decodeURIComponent(s))).join('/');
  const query = req.nextUrl.search || '';

  const domain = await prisma.anmDomain.findUnique({
    where: { name },
    include: { owner: { select: { address: true, displayName: true, isAgent: true } } },
  });

  if (!domain || domain.status !== 'ACTIVE') {
    return page(`${name}.anm — unregistered`, `
      <div class="badge">🌐 unregistered</div>
      <h1><span class="fqdn">${name}.anm</span></h1>
      <p>No one owns this name yet on the Animica Internet.</p>
      <div class="row"><a class="btn" href="${config.baseUrl}/names">Register ${name}.anm →</a>
      <a class="btn ghost" href="${config.baseUrl}/names">Search names</a></div>`, 404);
  }

  let records: any = {};
  try { records = JSON.parse(domain.recordsJson); } catch { /* ignore */ }

  // 1) Hosted content. A CID addresses a single object, so sub-paths resolve to the root object.
  if (domain.contentCid && !subPath) {
    const obj = await getObject(domain.contentCid);
    if (obj && verifyObject(domain.contentCid, obj.bytes)) {
      return new NextResponse(new Uint8Array(obj.bytes), {
        status: 200,
        headers: {
          'content-type': obj.mime,
          // Opaque-origin sandbox: hosted HTML/JS cannot read animica.dev cookies/storage/DOM.
          'content-security-policy': "sandbox allow-scripts allow-forms allow-popups allow-modals allow-popups-to-escape-sandbox",
          'x-content-type-options': 'nosniff',
          'referrer-policy': 'no-referrer',
          'x-anm-name': `${name}.anm`,
          'x-anm-cid': domain.contentCid,
          'x-anm-gateway': config.nodeId,
        },
      });
    }
    // fall through to info card if content missing/tampered
  }

  // 2) Endpoint redirect — preserve the deep-link sub-path + query string.
  const endpoint = typeof records.endpoint === 'string' ? records.endpoint : (typeof records.url === 'string' ? records.url : '');
  if (/^https?:\/\//i.test(endpoint)) {
    const base = endpoint.replace(/\/+$/, '');
    const target = subPath ? `${base}/${subPath}${query}` : `${endpoint}${query}`;
    return NextResponse.redirect(target, { status: 302, headers: { 'x-anm-name': `${name}.anm` } });
  }

  // 3) Resolver info card.
  const avatar = records.avatar || (domain.kind === 'agent' ? '🤖' : '🌐');
  const desc = records.description || `A ${domain.kind} on the Animica Internet.`;
  const owner = domain.owner.address;
  const recJson = JSON.stringify(records, null, 2);
  return page(`${name}.anm`, `
    <div class="badge">${avatar} ${domain.kind}${domain.owner.isAgent ? ' · agent' : ''}</div>
    <h1><span class="fqdn">${name}.anm</span></h1>
    <p>${desc.replace(/[<>]/g, '')}</p>
    <div><span class="pill">owner ${owner.slice(0, 16)}…</span>${domain.agentHandle ? `<span class="pill">@${domain.agentHandle}</span>` : ''}${domain.contentCid ? '<span class="pill">hosted</span>' : ''}</div>
    ${recJson !== '{}' ? `<div class="rec">${recJson.replace(/[<>]/g, (c) => (c === '<' ? '&lt;' : '&gt;'))}</div>` : ''}
    <div class="row"><a class="btn" href="${config.baseUrl}/api/mkt/v1/names/${name}">Resolver JSON</a>
    <a class="btn ghost" href="${config.baseUrl}/names">Explore .anm</a></div>`);
}
