// POST /app/api/pools/[poolId]/fund-confirm — proxy to /pool/fund/confirm.
// Body: { txid, requester }. Polled by the client until { funded: true }.
import { NextResponse } from "next/server";
import { z } from "zod";
import { coordinatorClient, CoordinatorError } from "@/server/coordinatorClient";

export const dynamic = "force-dynamic";

const Body = z.object({
  txid: z.string().min(1),
  requester: z.string().min(1),
});

export async function POST(
  req: Request,
  { params }: { params: { poolId: string } },
) {
  let body: z.infer<typeof Body>;
  try {
    body = Body.parse(await req.json());
  } catch {
    return NextResponse.json(
      { error: "txid and requester required" },
      { status: 400 },
    );
  }
  try {
    const data = await coordinatorClient.fundConfirm(
      params.poolId,
      body.txid,
      body.requester,
    );
    return NextResponse.json(data);
  } catch (err) {
    const status = err instanceof CoordinatorError ? err.status : 502;
    const message = err instanceof Error ? err.message : "confirm failed";
    return NextResponse.json(
      { error: message },
      { status: status >= 400 && status < 600 ? status : 502 },
    );
  }
}
