import { readFile } from "node:fs/promises";
import { NextResponse } from "next/server";
import { findPaper, findPaperArtifact } from "@/lib/paper-library";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const { slug } = await context.params;
  const paper = await findPaper(slug);
  if (!paper?.markdownUrl) {
    return NextResponse.json({ error: "markdown not found" }, { status: 404 });
  }
  try {
    const artifact = await findPaperArtifact(slug, "markdown");
    if (!artifact) throw new Error("markdown not found");
    const body = await readFile(artifact);
    return new NextResponse(body, {
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "content-disposition": `attachment; filename="${slug}-ja.md"`,
        "cache-control": "no-store",
      },
    });
  } catch {
    return NextResponse.json({ error: "markdown not found" }, { status: 404 });
  }
}
