const ARXIV_ID_PATTERN =
  /^(?<id>(?:\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}|[a-z][a-z0-9.-]{0,31}\/\d{7}))(?<version>v[1-9]\d{0,4})?$/i;

/** Normalize only a complete arXiv ID or an official HTTPS arXiv URL. */
export function normalizeArxivId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  let candidate = value.trim();

  if (candidate.includes("://")) {
    let parsed: URL;
    try {
      parsed = new URL(candidate);
    } catch {
      return null;
    }
    if (
      parsed.protocol !== "https:"
      || parsed.hostname.toLowerCase() !== "arxiv.org"
      || parsed.username !== ""
      || parsed.password !== ""
      || parsed.port !== ""
    ) {
      return null;
    }
    const pathMatch = /^\/(abs|html|pdf)\/(.+?)\/?$/i.exec(parsed.pathname);
    if (!pathMatch) return null;
    candidate = pathMatch[2];
    if (pathMatch[1].toLowerCase() === "pdf" && candidate.toLowerCase().endsWith(".pdf")) {
      candidate = candidate.slice(0, -4);
    }
  } else {
    candidate = candidate.replace(/^arxiv:\s*/i, "");
  }

  const match = ARXIV_ID_PATTERN.exec(candidate);
  if (!match?.groups) return null;
  const identifier = match.groups.id.includes("/")
    ? match.groups.id.toLowerCase()
    : match.groups.id;
  return `${identifier}${(match.groups.version ?? "").toLowerCase()}`;
}
