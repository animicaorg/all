import { NextResponse } from "next/server";

export async function POST() {
  return NextResponse.json({ error: "Mock wallet approval has been removed. Use real wallet callback flow." }, { status: 410 });
}
