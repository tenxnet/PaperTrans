import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { isPaperArtifactPublished } from "@/lib/paper-library";
import { getPaperTransRuntimeConfig } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".avif": "image/avif",
  ".css": "text/css; charset=utf-8",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
};
const ARTIFACT_CONTENT_SECURITY_POLICY = [
  "default-src 'none'",
  "base-uri 'none'",
  "connect-src 'none'",
  "font-src 'self'",
  "form-action 'none'",
  "frame-ancestors 'self'",
  "frame-src 'none'",
  "img-src 'self' data:",
  "manifest-src 'none'",
  "media-src 'self'",
  "object-src 'none'",
  "script-src 'none'",
  "style-src 'self' 'unsafe-inline'",
  "worker-src 'none'",
].join("; ");

export async function GET(
  request: Request,
  context: { params: Promise<{ slug: string; asset?: string[] }> },
) {
  const { slug, asset } = await context.params;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(slug)) {
    return NextResponse.json({ error: "invalid slug" }, { status: 400 });
  }
  if (!(await isPaperArtifactPublished(slug))) {
    return NextResponse.json({ error: "artifact not found" }, { status: 404 });
  }
  const publicationRoot = path.join(getPaperTransRuntimeConfig().outputRoot, slug, "html");
  const requested = path.resolve(publicationRoot, ...(asset?.length ? asset : ["index.html"]));
  if (requested !== publicationRoot && !requested.startsWith(`${publicationRoot}${path.sep}`)) {
    return NextResponse.json({ error: "invalid path" }, { status: 400 });
  }
  try {
    const [rootInfo, requestedInfo, canonicalRoot, canonicalRequested] = await Promise.all([
      lstat(publicationRoot),
      lstat(requested),
      realpath(publicationRoot),
      realpath(requested),
    ]);
    if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) throw new Error("invalid root");
    if (!requestedInfo.isFile() || requestedInfo.isSymbolicLink()) throw new Error("not a file");
    if (
      canonicalRequested === canonicalRoot
      || !canonicalRequested.startsWith(`${canonicalRoot}${path.sep}`)
    ) throw new Error("artifact escaped publication root");
    const body = await readFile(requested);
    const isHtml = path.extname(requested).toLowerCase() === ".html";
    const isEmbeddedHtml = isHtml && new URL(request.url).searchParams.get("embed") === "1";
    const html = isHtml ? body.toString("utf8") : "";
    const compatibilityCss = isHtml && !html.includes("data-papertrans-browser-compat")
      ? "<style data-papertrans-browser-compat>html,body{width:100%;max-width:100%;overflow-x:hidden}body{display:block!important}.ptx-shell,.ptx-main,.ptx-main figure,.ptx-main .ltx_table,.ptx-main .ltx_flex_figure,.ptx-main .ltx_transformed_outer{min-width:0;max-width:100%}.ptx-main{width:100%;overflow:hidden}.ptx-main .ltx_flex_figure>*{min-width:0}.ptx-main figure img,.ptx-main figure object,.ptx-main figure svg{max-width:100%;height:auto}</style>"
      : "";
    const embedCss = isEmbeddedHtml
      ? "<style data-papertrans-embed>.ptx-topbar,.ptx-toc,body>.topbar,body>.shell>aside{display:none!important}html,body{display:block!important;width:100%!important;min-width:0!important;max-width:none!important;margin:0!important}html{scroll-padding-top:20px!important}.ptx-shell,body>.shell{box-sizing:border-box!important;display:block!important;grid-column:auto!important;grid-template-columns:none!important;width:100%!important;min-width:0!important;max-width:none!important;margin:0!important;padding:20px!important}.ptx-main,body>.shell>main{box-sizing:border-box!important;display:block!important;width:100%!important;min-width:0!important;max-width:none!important;margin:0!important}</style>"
      : "";
    const responseBody = isHtml
      ? html.replace(
        "</head>",
        `${compatibilityCss}${embedCss}</head>`,
      )
      : body;
    return new NextResponse(responseBody, {
      headers: {
        "content-type": TYPES[path.extname(requested).toLowerCase()] ?? "application/octet-stream",
        "cache-control": "no-store",
        "content-security-policy": ARTIFACT_CONTENT_SECURITY_POLICY,
        "permissions-policy": "camera=(), geolocation=(), microphone=()",
        "referrer-policy": "no-referrer",
        "x-content-type-options": "nosniff",
      },
    });
  } catch {
    return NextResponse.json({ error: "artifact not found" }, { status: 404 });
  }
}
