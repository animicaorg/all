import { NextResponse } from 'next/server';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/browser/download -> the packaged Animica Internet browser extension (.zip).
// Served under /api/mkt/ so it rides the existing marketplace proxy (no extra nginx location).
export async function GET() {
  try {
    const path = join(process.cwd(), 'extension', 'dist', 'animica-browser.zip');
    const buf = await readFile(path);
    return new NextResponse(new Uint8Array(buf), {
      status: 200,
      headers: {
        'content-type': 'application/zip',
        'content-disposition': 'attachment; filename="animica-browser.zip"',
        'cache-control': 'public, max-age=300',
      },
    });
  } catch {
    return NextResponse.json({ error: { code: 'not_built', message: 'extension package not found' } }, { status: 404 });
  }
}
