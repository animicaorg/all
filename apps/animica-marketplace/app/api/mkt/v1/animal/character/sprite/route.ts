import { NextRequest, NextResponse } from 'next/server';
import { requireConsole, baseUrl } from '@/lib/animalApi';
import { putObject } from '@/lib/storage';
import { getCharacter, saveCharacter } from '@/lib/animalCharacter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const MAX_PNG = 6 * 1024 * 1024; // 6 MiB — a character sprite, not a video
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

// POST a PNG (multipart 'file', or raw image/png body) to give the character a custom look.
// The image is stored content-addressed (immutable, hash-named) and served from /content/<cid>;
// the stream worker downloads it and renders it as the on-screen sprite. Animica's cat is the
// default — this only overrides it for whoever owns this console.
export async function POST(req: NextRequest) {
  const unauth = await requireConsole();
  if (unauth) return unauth;

  let bytes: Buffer | null = null;
  const ctype = req.headers.get('content-type') || '';
  try {
    if (ctype.includes('multipart/form-data')) {
      const form = await req.formData();
      const f = form.get('file');
      if (f && typeof f !== 'string') bytes = Buffer.from(await (f as Blob).arrayBuffer());
    } else {
      bytes = Buffer.from(await req.arrayBuffer());
    }
  } catch {
    return NextResponse.json({ error: 'could not read upload' }, { status: 400 });
  }

  if (!bytes || bytes.length === 0) return NextResponse.json({ error: 'no image uploaded' }, { status: 400 });
  if (bytes.length > MAX_PNG) return NextResponse.json({ error: `image too large (max ${MAX_PNG} bytes)` }, { status: 413 });
  if (!bytes.subarray(0, 8).equals(PNG_MAGIC)) {
    return NextResponse.json({ error: 'only PNG images are accepted (transparent background recommended)' }, { status: 415 });
  }

  const { cid } = await putObject(bytes, 'image/png', 'animal-sprite');
  const sprite_url = `${baseUrl()}/api/mkt/v1/content/${cid}`;
  const character = await saveCharacter({ ...(await getCharacter()), kind: 'sprite', sprite_url });
  return NextResponse.json({ ok: true, cid, sprite_url, character });
}
