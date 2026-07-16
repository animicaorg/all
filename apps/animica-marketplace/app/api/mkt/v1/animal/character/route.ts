import { NextRequest, NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { DEFAULT_CHARACTER, editCharacterViaPrompt, getCharacter, saveCharacter } from '@/lib/animalCharacter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET the current character sheet; POST to edit it — by a natural-language { prompt }
// ("make her a sassy blue dragon who loves DeFi"), a structured { patch } (palette
// pickers, name, voice sliders), or { reset:true } to restore the Animica cat.
export async function GET() {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  return NextResponse.json({ character: await getCharacter(), default: DEFAULT_CHARACTER });
}

export async function POST(req: NextRequest) {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  let body: any = {};
  try { body = await req.json(); } catch { /* ignore */ }

  if (body?.reset) {
    return NextResponse.json({ ok: true, character: await saveCharacter(DEFAULT_CHARACTER) });
  }
  if (typeof body?.prompt === 'string' && body.prompt.trim()) {
    const character = await editCharacterViaPrompt(body.prompt.trim().slice(0, 1000));
    return NextResponse.json({ ok: true, character });
  }
  if (body?.patch && typeof body.patch === 'object') {
    const character = await saveCharacter(body.patch);
    return NextResponse.json({ ok: true, character });
  }
  return NextResponse.json({ error: 'send { prompt }, { patch }, or { reset:true }' }, { status: 400 });
}
