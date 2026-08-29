import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { findPaper } from "@/lib/paper-library";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const { slug } = await context.params;
  const paper = await findPaper(slug);
  if (!paper?.downloadUrl) {
    return NextResponse.json({ error: "bundle not found" }, { status: 404 });
  }
  try {
    const filename = `${slug}-html.zip`;
    const body = await readFile(path.join(process.cwd(), "output", slug, filename));
    return new NextResponse(body, {
      headers: {
        "content-type": "application/zip",
        "content-disposition": `attachment; filename="${filename}"`,
        "cache-control": "no-store",
      },
    });
  } catch {
    return NextResponse.json({ error: "bundle not found" }, { status: 404 });
  }
}

