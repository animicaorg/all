import { NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Recent mascot content (previews + live posts) for the console activity feed.
export async function GET() {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  const rows = await prisma.animalPost.findMany({ orderBy: { createdAt: 'desc' }, take: 40 });
  return NextResponse.json({ posts: rows });
}
