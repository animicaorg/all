import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { checkEntitlement } from '@/lib/entitlement';
import { verifyDownloadToken } from '@/lib/downloadToken';
import { fileResponse } from '@/lib/mediaStore';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/download/[token] -> stream the APK/digital-good bytes with Range
// support (resumable wallet downloads). The token (10-min HMAC, minted by download-token)
// names WHO fetches WHICH build; entitlement is RE-checked here so a refund between mint
// and redemption still blocks, and the build must still be APPROVED (delist blocks too).
// nginx: dedicated ^~ location, proxy_buffering off, 600s read timeout, NO sub_filter —
// served bytes must hash to AppBuild.sha3 (see deploy/animica.dev-marketplace.nginx.conf).

function fail(status: number, code: string, message: string) {
  return NextResponse.json({ error: { code, message } }, { status });
}

export async function GET(req: NextRequest, { params }: { params: { token: string } }) {
  try {
    const t = verifyDownloadToken(params.token);
    if (!t) return fail(403, 'bad_token', 'invalid or expired download token');

    const build = await prisma.appBuild.findUnique({
      where: { id: t.buildId },
      include: { listing: { select: { id: true, slug: true, type: true } } },
    });
    if (!build) return fail(404, 'not_found', 'build not found');
    if (build.status !== 'APPROVED') return fail(410, 'not_available', 'build is no longer available');

    const ent = await checkEntitlement(t.accountId, build.listingId);
    if (!ent.entitled) return fail(403, 'not_entitled', 'entitlement no longer active');

    const size = Number(build.sizeBytes);
    const range = parseRange(req.headers.get('range'), size);
    if (range === 'unsatisfiable') {
      return new NextResponse(null, { status: 416, headers: { 'Content-Range': `bytes */${size}`, 'Accept-Ranges': 'bytes' } });
    }

    const isApk = build.listing.type === 'APP';
    const mime = isApk ? 'application/vnd.android.package-archive' : 'application/octet-stream';
    const filename = isApk
      ? `${build.packageName}-${build.versionName}.apk`
      : `${build.listing.slug}-${build.versionName}.bin`;
    try {
      return fileResponse(build.path, { mime, bytes: size, filename, range });
    } catch {
      return fail(410, 'gone', 'build file no longer on disk');
    }
  } catch {
    return fail(500, 'internal', 'download failed');
  }
}

// Single-range parser. Returns null for no/unsupported Range header (=> full 200 body, the
// RFC 9110-sanctioned fallback for ranges we don't parse, incl. multi-range), inclusive
// offsets for a satisfiable range, and 'unsatisfiable' when start is past EOF (=> 416).
function parseRange(header: string | null, size: number): { start: number; end: number } | null | 'unsatisfiable' {
  if (!header || size <= 0) return null;
  const m = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!m) return null;
  const [, a, b] = m;
  if (a === '' && b === '') return null;
  if (a === '') {
    // suffix range: last N bytes
    const suffix = Number(b);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) return null;
    return { start: Math.max(0, size - suffix), end: size - 1 };
  }
  const start = Number(a);
  if (!Number.isSafeInteger(start)) return null;
  if (start >= size) return 'unsatisfiable';
  const end = b === '' ? size - 1 : Math.min(Number(b), size - 1);
  if (!Number.isSafeInteger(end) || end < start) return null;
  return { start, end };
}
