// GET /app/api/pools?status= — proxy to the ENA coordinator's /pool/list.
// Keeps ENA_COORDINATOR_BASE_URL server-side; the browser only ever talks here.
import { NextResponse } from "next/server";
import { coordinatorClient, CoordinatorError } from "@/server/coordinatorClient";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const statusFilter = new URL(req.url).searchParams.get("status") ?? undefined;
  try {
    const data = await coordinatorClient.listPools(statusFilter || undefined);
    return NextResponse.json(data);
  } catch (err) {
    const code = err instanceof CoordinatorError ? err.status : 502;
    return NextResponse.json(
      { error: "coordinator unavailable", pools: [] },
      { status: code >= 400 && code < 600 ? code : 502 },
    );
  }
}
