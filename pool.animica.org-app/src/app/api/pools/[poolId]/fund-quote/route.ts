// POST /app/api/pools/[poolId]/fund-quote — proxy to /pool/fund/quote.
// Body: { rewardAnm, requester }. Returns the coordinator's quote
// (treasury_address, required_nano, memo, …) for the wallet to pay.
import { NextResponse } from "next/server";
import { z } from "zod";
import { coordinatorClient, CoordinatorError } from "@/server/coordinatorClient";

export const dynamic = "force-dynamic";

const Body = z.object({
  rewardAnm: z.number().positive(),
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
      { error: "rewardAnm (positive number) and requester required" },
      { status: 400 },
    );
  }
  try {
    const data = await coordinatorClient.fundQuote(
      params.poolId,
      body.rewardAnm,
      body.requester,
    );
    return NextResponse.json(data);
  } catch (err) {
    const status = err instanceof CoordinatorError ? err.status : 502;
    const message = err instanceof Error ? err.message : "quote failed";
    return NextResponse.json(
      { error: message },
      { status: status >= 400 && status < 600 ? status : 502 },
    );
  }
}
