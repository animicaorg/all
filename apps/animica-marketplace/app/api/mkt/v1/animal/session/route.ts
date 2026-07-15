import { NextResponse } from 'next/server';
import { isConsoleAuthed, CONSOLE_USERNAME } from '@/lib/animalAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  const authed = await isConsoleAuthed();
  return NextResponse.json({ authed, user: authed ? CONSOLE_USERNAME : null });
}
