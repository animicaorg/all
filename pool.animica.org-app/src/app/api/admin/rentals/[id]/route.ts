// POST /app/api/admin/rentals/[id]  { to }
// Force a rental transition (resolve NEEDS_ADMIN_REVIEW, retry payout/refund,
// cancel, etc.). The worker then performs the actual payout/refund work for
// the target state — admins only nudge the state, transition() still enforces
// the legal-transition graph.
import { NextResponse } from "next/server";
import { z } from "zod";
import { requireAdmin } from "@/server/auth";
import { transition } from "@/server/rentals";
import { auditLog } from "@/lib/audit";

export const dynamic = "force-dynamic";

// Admin-safe targets. COMPLETING re-drives the owner payout; REFUND_DUE
// re-drives the refund; the rest are terminal/cancel resolutions.
const Body = z.object({
  to: z.enum(["COMPLETING", "REFUND_DUE", "COMPLETE", "CANCELLED", "FAILED", "NEEDS_ADMIN_REVIEW"]),
});

export async function POST(req: Request, { params }: { params: { id: string } }) {
  let admin;
  try {
    admin = await requireAdmin();
  } catch {
    return NextResponse.json({ error: "admin only" }, { status: 403 });
  }
  let body: z.infer<typeof Body>;
  try {
    body = Body.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "invalid target state" }, { status: 400 });
  }
  try {
    const updated = await transition({
      rentalId: params.id,
      to: body.to,
      actor: admin.email,
      reasonMetadata: { adminAction: true },
    });
    await auditLog({
      action: "rental.admin_transition",
      entityType: "Rental",
      entityId: params.id,
      actor: admin.email,
      metadata: { to: body.to },
    });
    return NextResponse.json({ rental: { id: updated.id, status: updated.status } });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 409 });
  }
}
