import { readFile } from "node:fs/promises";
import { NextResponse } from "next/server";
import { findPaperArtifact } from "@/lib/paper-library";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const { slug } = await context.params;
  try {
    const artifact = await findPaperArtifact(slug, "translatedPdf");
    if (!artifact) throw new Error("translated PDF not found");
    const body = await readFile(artifact);
    return new NextResponse(body, {
      headers: {
        "content-type": "application/pdf",
        "content-disposition": `inline; filename="${slug}-ja.pdf"`,
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      },
    });
  } catch {
    return NextResponse.json({ error: "translated PDF not found" }, { status: 404 });
  }
}
