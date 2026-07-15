import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { verifyWalletLogin } from '@/lib/wallet-verify';
import { VPN_ABUSE_THRESHOLD } from '@/lib/config';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/vpn/abuse { exitId, reason, detail, address, publicKey, sig }  (scope: vpn)
// File a signed abuse report against an exit. Message the wallet signed is
//   abuseMsg = "abuse:"+exitId+"|"+reason
// If enough OPEN reports accumulate, the exit is auto-taken offline pending review.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    const body = await req.json().catch(() => ({}));

    return await withIdempotency(req, ctx, body, async () => {
      const exitId = String(body.exitId ?? '').trim();
      const reason = String(body.reason ?? '').trim().slice(0, 200);
      const detail = String(body.detail ?? '').trim().slice(0, 2000);
      if (!reason) throw new ApiError(400, 'bad_report', 'reason required');

      const address = String(body.address ?? '').trim().toLowerCase();
      const publicKey = String(body.publicKey ?? '');
      const sig = String(body.sig ?? '');
      if (!address || !publicKey || !sig) throw new ApiError(400, 'unsigned', 'address, publicKey and sig required');

      const abuseMsg = `abuse:${exitId}|${reason}`;
      const v = verifyWalletLogin({ address, message: abuseMsg, signatureHex: sig, publicKeyHex: publicKey });
      if (!v.ok) throw new ApiError(401, 'bad_signature', v.reason ?? 'signature verification failed');

      let exit = null as null | Awaited<ReturnType<typeof prisma.vpnExit.findUnique>>;
      if (exitId) {
        exit = await prisma.vpnExit.findUnique({ where: { id: exitId } });
        if (!exit) throw new ApiError(404, 'not_found', 'no such exit');
      }

      // DEDUP: one OPEN report per (exit, reporter). Otherwise one wallet stacks N rows and trips
      // the threshold alone; here a repeat filing just refreshes the reporter's existing report.
      let report;
      if (exit) {
        const existing = await prisma.vpnAbuseReport.findFirst({
          where: { exitId: exit.id, reporterAddress: address, status: 'OPEN' },
        });
        report = existing
          ? await prisma.vpnAbuseReport.update({ where: { id: existing.id }, data: { reason, detail } })
          : await prisma.vpnAbuseReport.create({ data: { exitId: exit.id, reporterAddress: address, reason, detail } });
      } else {
        report = await prisma.vpnAbuseReport.create({ data: { exitId: null, reporterAddress: address, reason, detail } });
      }

      let takenOffline = false;
      if (exit && exit.online) {
        // Only DISTINCT reporters who ACTUALLY LEASED this exit count toward auto-offline. This binds
        // the takedown power to real, metered users of the exit, so free Sybil identities (which cost
        // nothing to mint) can file reports for the record but cannot delist an honest operator.
        const openReporters = await prisma.vpnAbuseReport.findMany({
          where: { exitId: exit.id, status: 'OPEN' },
          distinct: ['reporterAddress'],
          select: { reporterAddress: true },
        });
        const addrs = openReporters.map((r) => r.reporterAddress.toLowerCase());
        const leased = await prisma.vpnSession.findMany({
          where: { exitId: exit.id, clientAccount: { address: { in: addrs } } },
          distinct: ['clientAccountId'],
          select: { clientAccount: { select: { address: true } } },
        });
        const leasedSet = new Set(leased.map((s) => s.clientAccount.address.toLowerCase()));
        const qualified = addrs.filter((a) => leasedSet.has(a)).length;
        if (qualified >= VPN_ABUSE_THRESHOLD) {
          await prisma.vpnExit.update({ where: { id: exit.id }, data: { online: false } }).catch(() => {});
          takenOffline = true;
        }
      }

      return {
        status: 201,
        data: { reported: true, reportId: report.id, exitTakenOffline: takenOffline },
      };
    });
  } catch (e) {
    return err(e);
  }
}
