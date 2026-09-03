type RequestMetadata = Pick<Request, "headers" | "url">;

type LoopbackAuthority = Readonly<{
  hostname: "127.0.0.1" | "localhost" | "[::1]";
  port: number | null;
}>;

export type LocalMutationValidation =
  | Readonly<{ ok: true }>
  | Readonly<{
      ok: false;
      status: 403 | 415;
      code: "LOCAL_REQUEST_REQUIRED" | "UNSUPPORTED_MEDIA_TYPE";
      error: string;
    }>;

function parseLoopbackAuthority(value: string | null): LoopbackAuthority | null {
  if (!value) return null;
  const candidate = value.toLowerCase();
  const match = /^(127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?$/.exec(candidate);
  if (!match) return null;
  const port = match[2] === undefined ? null : Number(match[2]);
  if (port !== null && (port < 1 || port > 65_535)) return null;
  return {
    hostname: match[1] as LoopbackAuthority["hostname"],
    port,
  };
}

function effectivePort(protocol: string, port: number | null): number | null {
  if (port !== null) return port;
  if (protocol === "http:") return 80;
  if (protocol === "https:") return 443;
  return null;
}

/**
 * Accept only the literal loopback authorities supported by the source-checkout
 * server. In particular, do not trust Forwarded or X-Forwarded-Host: accepting
 * those would reopen DNS-rebinding access to local papers and mutation routes.
 */
export function isAllowedLoopbackHost(value: string | null): boolean {
  return parseLoopbackAuthority(value) !== null;
}

/**
 * Validate a browser mutation without breaking local curl/SDK clients.
 *
 * Browsers must identify the request as same-origin when Fetch Metadata is
 * present, and any Origin must exactly match the raw loopback Host. Non-browser
 * clients may omit both headers, but they still need an explicitly allowed
 * non-safelisted media type at each call site.
 */
export function validateLocalMutationRequest(
  request: RequestMetadata,
  allowedMediaTypes: readonly string[],
): LocalMutationValidation {
  const requestAuthority = parseLoopbackAuthority(request.headers.get("host"));
  if (requestAuthority === null) {
    return {
      ok: false,
      status: 403,
      code: "LOCAL_REQUEST_REQUIRED",
      error: "request must use a supported loopback host",
    };
  }

  let requestProtocol: string;
  try {
    requestProtocol = new URL(request.url).protocol;
  } catch {
    requestProtocol = "";
  }
  if (requestProtocol !== "http:" && requestProtocol !== "https:") {
    return {
      ok: false,
      status: 403,
      code: "LOCAL_REQUEST_REQUIRED",
      error: "request must use a supported local HTTP origin",
    };
  }

  const fetchSite = request.headers.get("sec-fetch-site")?.trim().toLowerCase();
  if (fetchSite !== undefined && fetchSite !== "same-origin") {
    return {
      ok: false,
      status: 403,
      code: "LOCAL_REQUEST_REQUIRED",
      error: "cross-origin browser requests are not allowed",
    };
  }

  const origin = request.headers.get("origin");
  if (origin !== null) {
    let originUrl: URL;
    try {
      originUrl = new URL(origin);
    } catch {
      return {
        ok: false,
        status: 403,
        code: "LOCAL_REQUEST_REQUIRED",
        error: "request origin is invalid",
      };
    }
    const originAuthority = parseLoopbackAuthority(originUrl.host);
    if (
      originUrl.protocol !== requestProtocol
      || originUrl.username !== ""
      || originUrl.password !== ""
      || originUrl.pathname !== "/"
      || originUrl.search !== ""
      || originUrl.hash !== ""
      || originAuthority === null
      || originAuthority.hostname !== requestAuthority.hostname
      || effectivePort(originUrl.protocol, originAuthority.port)
        !== effectivePort(requestProtocol, requestAuthority.port)
    ) {
      return {
        ok: false,
        status: 403,
        code: "LOCAL_REQUEST_REQUIRED",
        error: "cross-origin browser requests are not allowed",
      };
    }
  }

  const mediaType = request.headers.get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (!mediaType || !allowedMediaTypes.includes(mediaType)) {
    return {
      ok: false,
      status: 415,
      code: "UNSUPPORTED_MEDIA_TYPE",
      error: `content type must be ${allowedMediaTypes.join(" or ")}`,
    };
  }
  return { ok: true };
}
