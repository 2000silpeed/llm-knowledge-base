import { NextResponse } from "next/server";
import { buildGraphData } from "@/lib/wiki";

export const dynamic = "force-dynamic";

export function GET() {
  const data = buildGraphData();
  return NextResponse.json(data);
}
