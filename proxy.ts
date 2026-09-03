import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { isAllowedLoopbackHost } from "./lib/local-http-boundary";

/** Reject hostile Host headers before pages, artifacts, or APIs are reached. */
export function proxy(request: NextRequest) {
  if (!isAllowedLoopbackHost(request.headers.get("host"))) {
    return new NextResponse("This local PaperTrans server requires a loopback Host.\n", {
      status: 421,
      headers: {
        "cache-control": "no-store",
        "content-type": "text/plain; charset=utf-8",
        "x-content-type-options": "nosniff",
      },
    });
  }
  return NextResponse.next();
}
