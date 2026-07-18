import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { PUBLIC_CORS, publicPreflight } from '@/lib/api';
import { fileResponse } from '@/lib/mediaStore';
import { sweep } from '@/lib/mediaQueue';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// 9.0.0: download a finished job's disk artifact. Public by unguessable job id — the same
// trust model as the poll route. Unlike classic private inline results these are NOT
// once-wiped (big downloads can fail halfway); they die at the job's TTL instead.

const EXT_FOR: Record<string, string> = {
  'video/mp4': 'mp4', 'video/webm': 'webm', 'application/zip': 'zip',
  'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'image/png': 'png',
};

export function OPTIONS() {
  return publicPreflight();
}

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  await sweep();
  const job = await prisma.mediaJob.findUnique({
    where: { id: params.id },
    select: { id: true, kind: true, status: true, resultPath: true, resultBytes: true, resultMime: true, deliveredAt: true },
  });
  if (!job || job.status !== 'DONE' || !job.resultPath) {
    return NextResponse.json({ error: { code: 'not_found', message: 'No artifact for this job (missing, not finished, or expired).' } }, { status: 404, headers: PUBLIC_CORS });
  }
  if (!job.deliveredAt) {
    await prisma.mediaJob.update({ where: { id: job.id }, data: { deliveredAt: new Date() } }).catch(() => {});
  }
  const mime = job.resultMime || 'application/octet-stream';
  const ext = EXT_FOR[mime] || 'bin';
  try {
    const res = fileResponse(job.resultPath, {
      mime,
      bytes: job.resultBytes != null ? Number(job.resultBytes) : undefined,
      filename: `animica-${job.kind}-${job.id.slice(-8)}.${ext}`,
    });
    for (const [k, v] of Object.entries(PUBLIC_CORS)) res.headers.set(k, v as string);
    return res;
  } catch {
    return NextResponse.json({ error: { code: 'gone', message: 'Artifact file no longer on disk.' } }, { status: 410, headers: PUBLIC_CORS });
  }
}
