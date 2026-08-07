import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { SENSITIVE_CAPABILITIES } from '@/lib/cloud/config';
import { findPublishedApp, parseCaps, parseExpiry, parseNanm, parsePayees, validateSpendGrant, dayKeyUtc } from '../../_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/apps/[slug]/authorize — the user's explicit, revocable consent (§19, §20).
//
// The CloudGrant this writes is what the executor's capability broker checks ATOMICALLY on
// every sensitive host call. For SPEND_ANM the caps are mandatory and fail-closed: no finite
// per-call cap, no daily cap, or no expiry means no grant. Re-authorizing updates the grant in
// place and clears a previous revocation. Revoke from the dashboard: DELETE /api/cloud/v1/grants.

function grantView(g: {
  id: string;
  subjectKind: string;
  capabilities: string[];
  maxPerCallNanm: bigint;
  maxPerExecNanm: bigint;
  dailyCapNanm: bigint;
  spentTodayNanm: bigint;
  spendDayKey: string;
  allowedPayees: string[];
  expiresAt: Date | null;
  revokedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
}) {
  return {
    id: g.id,
    subjectKind: g.subjectKind,
    capabilities: g.capabilities,
    maxPerCallNanm: g.maxPerCallNanm,
    maxPerExecNanm: g.maxPerExecNanm,
    dailyCapNanm: g.dailyCapNanm,
    spentTodayNanm: g.spendDayKey === dayKeyUtc() ? g.spentTodayNanm : 0n,
    allowedPayees: g.allowedPayees,
    expiresAt: g.expiresAt,
    revoked: Boolean(g.revokedAt),
    createdAt: g.createdAt,
    updatedAt: g.updatedAt,
  };
}

export async function GET(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'read');
    const app = await findPublishedApp(decodeURIComponent(ctx.params.slug).trim().toLowerCase());
    if (!app) throw new ApiError(404, 'not_found', 'no such app');
    const grant = await prisma.cloudGrant.findUnique({
      where: { accountId_subjectKind_subjectId: { accountId: auth.accountId, subjectKind: 'app', subjectId: app.id } },
    });
    return ok({
      app: app.slug,
      declaredCapabilities: app.capabilities,
      sensitiveCapabilities: app.capabilities.filter((c) => (SENSITIVE_CAPABILITIES as string[]).includes(c)),
      grant: grant ? grantView(grant) : null,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');

    const app = await findPublishedApp(decodeURIComponent(ctx.params.slug).trim().toLowerCase());
    if (!app) throw new ApiError(404, 'not_found', 'no such app');

    const body = await req.json().catch(() => ({}));
    const capabilities = parseCaps(body?.capabilities);
    if (capabilities.length === 0) {
      throw new ApiError(400, 'bad_request', 'capabilities is required — list what you are authorizing');
    }
    const undeclared = capabilities.filter((c) => !app.capabilities.includes(c));
    if (undeclared.length > 0) {
      throw new ApiError(
        400,
        'undeclared_capability',
        `this app does not declare: ${undeclared.join(', ')} — you can only authorize what the app declares`,
      );
    }

    // Authorizing spend requires a key that can move money; everything else needs 'use'.
    requireScope(auth, capabilities.includes('SPEND_ANM') ? 'buy' : 'use');

    const maxPerCallNanm = body?.maxPerCallNanm != null ? parseNanm(body.maxPerCallNanm, 'maxPerCallNanm') : 0n;
    const maxPerExecNanm = body?.maxPerExecNanm != null ? parseNanm(body.maxPerExecNanm, 'maxPerExecNanm') : 0n;
    const dailyCapNanm = body?.dailyCapNanm != null ? parseNanm(body.dailyCapNanm, 'dailyCapNanm') : 0n;
    const allowedPayees = parsePayees(body?.allowedPayees);
    const expiresAt = parseExpiry(body?.expiresAt);

    if (capabilities.includes('SPEND_ANM')) {
      validateSpendGrant({ maxPerCallNanm, maxPerExecNanm, dailyCapNanm, allowedPayees, expiresAt });
    }

    const grant = await prisma.cloudGrant.upsert({
      where: { accountId_subjectKind_subjectId: { accountId: auth.accountId, subjectKind: 'app', subjectId: app.id } },
      create: {
        accountId: auth.accountId,
        subjectKind: 'app',
        subjectId: app.id,
        capabilities,
        maxPerCallNanm,
        maxPerExecNanm,
        dailyCapNanm,
        allowedPayees,
        expiresAt,
      },
      update: {
        capabilities,
        maxPerCallNanm,
        maxPerExecNanm,
        dailyCapNanm,
        allowedPayees,
        expiresAt,
        revokedAt: null,
      },
    });

    return ok(
      {
        authorized: true,
        app: app.slug,
        grant: grantView(grant),
        note: 'revoke any time from GET/DELETE /api/cloud/v1/grants — enforcement is atomic at every spend',
      },
      { status: 201 },
    );
  } catch (e) {
    return err(e);
  }
}
