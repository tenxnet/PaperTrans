import { NextResponse } from "next/server";
import { findPaper, savePaperLibraryState } from "@/lib/paper-library";

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
    const body = (await request.json()) as {
      tags?: unknown;
      isRead?: unknown;
      favorite?: unknown;
    };
    return NextResponse.json({ slug, ...(await savePaperLibraryState(slug, body)) });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "invalid library state" },
      { status: 400 },
    );
  }
}
