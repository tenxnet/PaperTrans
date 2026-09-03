import { NextResponse } from "next/server";
import { LibraryMetadataError } from "@/lib/library-state";
import { scanPaperLibrary } from "@/lib/paper-library";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return NextResponse.json({ papers: await scanPaperLibrary() });
  } catch (error) {
    if (error instanceof LibraryMetadataError) {
      return NextResponse.json(
        { code: "LIBRARY_METADATA_INVALID", error: error.message },
        { status: 500 },
      );
    }
    throw error;
  }
}
