import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".css": "text/css; charset=utf-8",
};

export async function GET(
  request: Request,
  context: { params: Promise<{ slug: string; asset?: string[] }> },
) {
  const { slug, asset } = await context.params;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(slug)) {
    return NextResponse.json({ error: "invalid slug" }, { status: 400 });
  }
  const publicationRoot = path.resolve(process.cwd(), "output", slug, "html");
  const requested = path.resolve(publicationRoot, ...(asset?.length ? asset : ["index.html"]));
  if (requested !== publicationRoot && !requested.startsWith(`${publicationRoot}${path.sep}`)) {
    return NextResponse.json({ error: "invalid path" }, { status: 400 });
  }
  try {
    if (!(await stat(requested)).isFile()) throw new Error("not a file");
    const body = await readFile(requested);
    const isEmbeddedHtml = path.extname(requested).toLowerCase() === ".html"
      && new URL(request.url).searchParams.get("embed") === "1";
    const responseBody = isEmbeddedHtml
      ? body.toString("utf8").replace(
        "</head>",
        "<style data-papertrans-embed>.ptx-topbar{display:none!important}html{scroll-padding-top:20px!important}.ptx-shell{padding-top:20px!important}</style></head>",
      )
      : body;
    return new NextResponse(responseBody, {
      headers: {
        "content-type": TYPES[path.extname(requested).toLowerCase()] ?? "application/octet-stream",
        "cache-control": "no-store",
      },
    });
  } catch {
    return NextResponse.json({ error: "artifact not found" }, { status: 404 });
  }
}
