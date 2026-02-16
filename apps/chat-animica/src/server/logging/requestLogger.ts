import { randomUUID } from "crypto";
import { NextRequest } from "next/server";
import { withRequestId } from "@/src/server/logging/logger";

export function getRequestLogger(req: NextRequest) {
  const requestId = req.headers.get("x-request-id") ?? randomUUID();
  return withRequestId(requestId);
}
