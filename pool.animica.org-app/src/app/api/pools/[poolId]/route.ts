// GET /app/api/pools/[poolId] — proxy to the ENA coordinator's /pool/status.
import { NextResponse } from "next/server";
import { coordinatorClient, CoordinatorError } from "@/server/coordinatorClient";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: { poolId: string } },
) {
  try {
    const data = await coordinatorClient.poolStatus(params.poolId);
    return NextResponse.json(data);
  } catch (err) {
    const status = err instanceof CoordinatorError ? err.status : 502;
    return NextResponse.json(
      { error: "coordinator unavailable" },
      { status: status >= 400 && status < 600 ? status : 502 },
    );
  }
}
