import { NextResponse } from "next/server";
import { scanPaperLibrary } from "@/lib/paper-library";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ papers: await scanPaperLibrary() });
}

