import { NextRequest } from 'next/server';
import { ok, err, ApiError, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { getTransaction } from '@/lib/chain';
import { decodeAnimicaAddress, isAnimicaAddress } from '@/lib/address';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// 32-byte tx hash, optionally 0x-prefixed.
const HEX_TXID = /^(0x)?[0-9a-f]{64}$/i;

export function OPTIONS() {
  return publicPreflight();
}

// POST /api/mkt/v1/stream/item/[id]/tip
// Body: { txid, amountNanm (string, nANM base units), fromAddress }
// The wallet already broadcast an on-chain tip (creator <- tipper). We record it,
// dedupe on the unique txid, and count it exactly once.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    let body: any = {};
    try { body = await req.json(); } catch { /* empty body -> validation below fails */ }

    const txidRaw = typeof body.txid === 'string' ? body.txid.trim() : '';
    const fromAddress = typeof body.fromAddress === 'string' ? body.fromAddress.trim() : '';
    const amountStr = typeof body.amountNanm === 'string' ? body.amountNanm.trim()
      : (typeof body.amountNanm === 'number' ? String(body.amountNanm) : '');

    // ── validate inputs ───────────────────────────────────────────────────────
    if (!HEX_TXID.test(txidRaw)) throw new ApiError(400, 'bad_txid', 'txid must be a 32-byte hex hash');
    const txid = txidRaw.toLowerCase(); // normalize so 0xAB.. and 0xab.. dedupe as one
    if (!isAnimicaAddress(fromAddress)) throw new ApiError(400, 'bad_from', 'fromAddress is not a valid anim1 address');
    let amount: bigint;
    try { amount = BigInt(amountStr); } catch { throw new ApiError(400, 'bad_amount', 'amountNanm must be an integer string'); }
    if (amount <= 0n) throw new ApiError(400, 'bad_amount', 'amountNanm must be > 0');

    // ── find item ─────────────────────────────────────────────────────────────
    const item = await prisma.mediaItem.findUnique({ where: { id: params.id } });
    if (!item) throw new ApiError(404, 'not_found', 'media item not found');

    // ── LIGHT on-chain sanity check — NOT trusting execution (receipts are status:null,
    // headers commit zero stateRoot). We only assert INCLUSION shape: if the node already
    // knows this tx it must actually pay THIS creator at least the claimed amount. If the
    // node hasn't seen it yet we accept it as pending; the unique-txid dedupe below is what
    // prevents a replay / double count in either case.
    const tx = await getTransaction(txid).catch(() => null);
    if (tx) {
      const to = String(tx.to ?? '').replace(/^0x/i, '').toLowerCase();
      const expected = Buffer.from(decodeAnimicaAddress(item.ownerAddress).digest).toString('hex');
      let value = 0n;
      try { value = BigInt(tx.value ?? 0); } catch { /* leave 0 -> mismatch */ }
      if (to !== expected || value < amount) {
        throw new ApiError(400, 'tip_mismatch', 'tip does not match');
      }
    }

    // ── record once (idempotent on txid) and count in the same transaction ──────
    try {
      const updated = await prisma.$transaction(async (db) => {
        await db.mediaTip.create({
          data: { itemId: item.id, fromAddress, toAddress: item.ownerAddress, amountNanm: amount, txid },
        });
        return db.mediaItem.update({
          where: { id: item.id },
          data: { tipTotalNanm: { increment: amount }, tipCount: { increment: 1 } },
        });
      });
      return ok({ recorded: true, tipTotalNanm: updated.tipTotalNanm.toString() });
    } catch (e: any) {
      // unique(txid) conflict -> this tip was already recorded; return the current
      // total without incrementing again (fully idempotent).
      if (e?.code === 'P2002') {
        const cur = await prisma.mediaItem.findUnique({ where: { id: item.id }, select: { tipTotalNanm: true } });
        return ok({ recorded: true, tipTotalNanm: (cur?.tipTotalNanm ?? item.tipTotalNanm).toString() });
      }
      throw e;
    }
  } catch (e) {
    return err(e);
  }
}
