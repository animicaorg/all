import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { runtime as cloudRuntime } from '@/lib/cloud/config';
import { str } from '../../apps/_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/developers/handle — claim the public developer handle.
//
// The handle becomes the developer's public identity: /developers/<handle> and the execution
// URL /api/cloud/v1/fn/<handle>/<fn>. Claimed ONCE (support changes it later if ever), stored
// lowercase, unique case-insensitively, reserved words refused.

const HANDLE_RE = /^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])?$/; // 3-30 total, no edge hyphens

// Names that would collide with platform surfaces or impersonate the operator.
const RESERVED = new Set([
  'admin', 'administrator', 'animica', 'anm', 'api', 'app', 'apps', 'agent', 'agents', 'auth',
  'billing', 'blog', 'cloud', 'console', 'dashboard', 'dev', 'developer', 'developers', 'docs',
  'download', 'downloads', 'explore', 'faq', 'fn', 'foundation', 'founding', 'function',
  'functions', 'grants', 'help', 'internal', 'legal', 'login', 'logout', 'marketplace', 'mod',
  'moderator', 'official', 'ops', 'owner', 'platform', 'pricing', 'privacy', 'python', 'reports',
  'reviews', 'root', 'search', 'security', 'settings', 'signin', 'signup', 'stats', 'status',
  'store', 'support', 'system', 'team', 'terms', 'treasury', 'wallet', 'www',
]);

export async function POST(req: NextRequest) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'publish');

    const body = await req.json().catch(() => ({}));
    const handle = str(body?.handle, 30).toLowerCase();
    if (handle.length < 3 || !HANDLE_RE.test(handle)) {
      throw new ApiError(400, 'invalid_handle', 'handle must be 3-30 chars of [a-z0-9-], no leading/trailing hyphen');
    }
    if (RESERVED.has(handle) || handle.startsWith('anim1')) {
      throw new ApiError(400, 'reserved_handle', `"${handle}" is reserved`);
    }

    const me = await prisma.account.findUnique({ where: { id: auth.accountId }, select: { handle: true } });
    if (!me) throw new ApiError(404, 'not_found', 'account not found');
    if (me.handle) {
      if (me.handle === handle) {
        return ok({ claimed: true, handle, unchanged: true });
      }
      throw new ApiError(409, 'handle_already_set', `your handle is "${me.handle}" — handles are claimed once; contact support to change it`);
    }

    // Case-insensitive uniqueness (handles are stored lowercase, but check defensively).
    const taken = await prisma.account.findFirst({
      where: { handle: { equals: handle, mode: 'insensitive' } },
      select: { id: true },
    });
    if (taken) throw new ApiError(409, 'handle_taken', `"${handle}" is already claimed`);

    try {
      await prisma.account.update({ where: { id: auth.accountId }, data: { handle } });
    } catch (e: any) {
      if (e?.code === 'P2002') throw new ApiError(409, 'handle_taken', `"${handle}" is already claimed`);
      throw e;
    }

    return ok(
      {
        claimed: true,
        handle,
        profileUrl: `/api/cloud/v1/developers/${handle}`,
        functionBase: `${cloudRuntime.publicBase}/api/cloud/v1/fn/${handle}/`,
      },
      { status: 201 },
    );
  } catch (e) {
    return err(e);
  }
}
