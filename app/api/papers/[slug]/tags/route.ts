import { NextResponse } from "next/server";
import { findPaper, savePaperTags } from "@/lib/paper-library";

export const runtime = "nodejs";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const { slug } = await context.params;
  if (!(await findPaper(slug))) {
    return NextResponse.json({ error: "paper not found" }, { status: 404 });
  }
  try {
    const body = (await request.json()) as { tags?: unknown };
    return NextResponse.json({ slug, tags: await savePaperTags(slug, body.tags) });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "invalid tags" },
      { status: 400 },
    );
  }
}

