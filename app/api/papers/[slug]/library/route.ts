import { NextResponse } from "next/server";
import { LibraryMetadataBusyError, LibraryMetadataError } from "@/lib/library-state";
import { validateLocalMutationRequest } from "@/lib/local-http-boundary";
import { findPaper, savePaperLibraryState } from "@/lib/paper-library";

export const runtime = "nodejs";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const boundary = validateLocalMutationRequest(request, ["application/json"]);
  if (!boundary.ok) {
    return NextResponse.json(
      { code: boundary.code, error: boundary.error },
      { status: boundary.status },
    );
  }
  const { slug } = await context.params;
  try {
    if (!(await findPaper(slug))) {
      return NextResponse.json({ error: "paper not found" }, { status: 404 });
    }
    const body = (await request.json()) as {
      tags?: unknown;
      addTags?: unknown;
      removeTags?: unknown;
      isRead?: unknown;
      favorite?: unknown;
    };
    return NextResponse.json({ slug, ...(await savePaperLibraryState(slug, body)) });
  } catch (error) {
    if (error instanceof LibraryMetadataBusyError) {
      return NextResponse.json(
        { error: error.message },
        { status: 503, headers: { "Retry-After": "1" } },
      );
    }
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "invalid library state" },
      { status: error instanceof LibraryMetadataError ? 500 : 400 },
    );
  }
}
