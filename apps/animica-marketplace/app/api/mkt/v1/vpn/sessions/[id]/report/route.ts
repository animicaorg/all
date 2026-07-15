import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { verifyWalletLogin } from '@/lib/wallet-verify';
import { reconcileSession } from '@/lib/vpn';
import { nanmToAnm } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

function isUniqueViolation(e: unknown): boolean {
  return !!e && typeof e === 'object' && (e as any).code === 'P2002';
}

// POST /api/mkt/v1/vpn/sessions/[id]/report  (scope: vpn)
// A signed cumulative usage receipt from one side of a session. Message the wallet signed is
//   reportMsg = "usage:"+sessionId+"|"+side+"|"+seq+"|"+cumRx+"|"+cumTx+"|"+ts
// (the wallet actually signs "animica:signMessage:"+reportMsg — verifyWalletLogin re-adds the domain).
// Replay is rejected two ways: the @@unique([sessionId,side,seq]) row AND Idempotency-Key. When BOTH
// sides have reported, the server reconciles min(client,exit) and accrues the exit's IOU reward.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    const sessionId = params.id;
    const body = await req.json().catch(() => ({}));

    return await withIdempotency(req, ctx, body, async () => {
      if (body.sessionId && String(body.sessionId) !== sessionId) {
        throw new ApiError(400, 'bad_request', 'sessionId mismatch (path vs body)');
      }
      const side = String(body.side ?? '').trim();
      if (side !== 'client' && side !== 'exit') throw new ApiError(400, 'bad_side', "side must be 'client' or 'exit'");
      const seq = Math.floor(Number(body.seq));
      if (!Number.isInteger(seq) || seq < 0) throw new ApiError(400, 'bad_seq', 'seq must be a non-negative integer');

      // Keep the raw string forms so the reconstructed message is byte-exact with what was signed.
      const cumRxStr = String(body.cumRx ?? '');
      const cumTxStr = String(body.cumTx ?? '');
      const tsStr = String(body.ts ?? '');
      let cumRx: bigint, cumTx: bigint, ts: bigint;
      try {
        cumRx = BigInt(cumRxStr); cumTx = BigInt(cumTxStr); ts = BigInt(tsStr);
      } catch {
        throw new ApiError(400, 'bad_counters', 'cumRx/cumTx/ts must be integer strings');
      }
      if (cumRx < 0n || cumTx < 0n) throw new ApiError(400, 'bad_counters', 'counters must be non-negative');

      const address = String(body.address ?? '').trim().toLowerCase();
      const publicKey = String(body.publicKey ?? '');
      const sig = String(body.sig ?? '');
      if (!address || !publicKey || !sig) throw new ApiError(400, 'unsigned', 'address, publicKey and sig required');

      const session = await prisma.vpnSession.findUnique({
        where: { id: sessionId },
        include: {
          exit: { include: { owner: { select: { address: true } } } },
          clientAccount: { select: { address: true } },
        },
      });
      if (!session) throw new ApiError(404, 'not_found', 'no such session');
      if (session.status === 'CLOSED') throw new ApiError(409, 'session_closed', 'session is closed');

      // Bind the signer to the side: only the client wallet may post client receipts, only the exit
      // owner wallet may post exit receipts. Prevents a peer forging the counterparty's counter.
      const expectedAddr = side === 'client'
        ? session.clientAccount.address.toLowerCase()
        : session.exit.owner.address.toLowerCase();
      if (address !== expectedAddr) {
        throw new ApiError(403, 'wrong_signer', `address does not match the ${side} of this session`);
      }

      // Reconstruct the signed message server-side — never trust a client-supplied reportMsg.
      const reportMsg = `usage:${sessionId}|${side}|${seq}|${cumRxStr}|${cumTxStr}|${tsStr}`;
      const v = verifyWalletLogin({ address, message: reportMsg, signatureHex: sig, publicKeyHex: publicKey });
      if (!v.ok) throw new ApiError(401, 'bad_signature', v.reason ?? 'signature verification failed');

      // Persist the receipt; the unique (sessionId, side, seq) rejects replays.
      try {
        await prisma.vpnUsageReceipt.create({
          data: { sessionId, side, seq, cumRx, cumTx, ts, sig, address },
        });
      } catch (e) {
        if (isUniqueViolation(e)) throw new ApiError(409, 'replay', 'a receipt for this (side, seq) already exists');
        throw e;
      }

      // Reconcile if both sides have now reported. Returns null if the counterparty hasn't yet.
      const rec = await reconcileSession(sessionId);
      return {
        status: 201,
        data: {
          recorded: true, sessionId, side, seq,
          reconciled: rec
            ? {
                reconciledBytes: Math.floor(rec.reconciledBytes),
                flagged: rec.flagged,
                selfDeal: rec.selfDeal,
                sessionRewardAnm: nanmToAnm(rec.sessionRewardNanm),
                accruedAnm: nanmToAnm(rec.incrementNanm),
              }
            : null,
          note: rec
            ? 'reward accrued (pending settlement) as an IOU — not paid, not a spendable balance'
            : 'receipt stored; awaiting the counterparty receipt before reconciliation',
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}
