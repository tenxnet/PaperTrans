import { mkdir, readFile, readdir, realpath, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";

export type PaperStatus =
  | "prepared"
  | "translating"
  | "ready_to_finalize"
  | "completed"
  | "needs_review"
  | "failed";

export type PaperSummary = {
  slug: string;
  sourceType: string;
  title: string;
  authors: string[];
  publishedAt: string | null;
  requestedArxivId: string;
  resolvedArxivId: string;
  sourceUrl: string;
  provider: string;
  targetLanguage: "ja";
  status: PaperStatus;
  progress: { completed: number; total: number };
  tags: string[];
  isRead: boolean;
  favorite: boolean;
  updatedAt: string;
  createdAt: string;
  finalizedAt: string | null;
  artifactUrl: string | null;
  downloadUrl: string | null;
  qa: {
    status: "passed" | "failed" | "missing";
    figures: number;
    tables: number;
    math: number;
    bibliographyEntries: number;
    unresolvedInternalLinks: number;
    missingLocalAssets: number;
    browserChecked: boolean;
  };
};

type Manifest = {
  schemaVersion?: string | number;
  jobId?: string;
  status?: string;
  provider?: string;
  sourceType?: string;
  settings?: {
    targetLanguage?: string;
  };
  paper?: {
    requestedArxivId?: string;
    resolvedArxivId?: string;
    title?: string;
    sourceUrl?: string;
    authors?: string[];
    publishedAt?: string | null;
  };
  chunks?: Array<{ status?: string }>;
  artifacts?: {
    html?: string;
    qa?: string;
    bundle?: string;
    translatedPdf?: string;
    indexPath?: string;
    bundlePath?: string;
    artifactRoute?: string;
  };
  createdAt?: string;
  updatedAt?: string;
  finalizedAt?: string | null;
};

type QaDocument = {
  status?: string;
  output?: {
    figures?: number;
    tables?: number;
    visibleMath?: number;
    math?: number;
    bibliographyEntries?: number;
  };
  unresolvedInternalLinks?: number;
  missingLocalAssets?: unknown[];
  browserDom?: unknown;
};

type LibraryMetadata = {
  version: 1;
  papers: Record<string, PaperLibraryState>;
};

export type PaperLibraryState = {
  tags: string[];
  isRead: boolean;
  favorite: boolean;
};

export type PaperLibraryPatch = {
  tags?: unknown;
  isRead?: unknown;
  favorite?: unknown;
};

const JOB_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const REPO_ROOT = process.cwd();
const OUTPUT_ROOT = path.join(REPO_ROOT, "output");
const DATA_ROOT = path.join(REPO_ROOT, "data");
const LIBRARY_METADATA = path.join(DATA_ROOT, "library.json");
const MANIFEST_FILENAMES = ["papertrans-job.json", "mcp-job.json", "chatgpt-job.json"] as const;

type ArtifactKind = "html" | "qa" | "bundle" | "translatedPdf";

const ARTIFACT_EXTENSIONS: Record<ArtifactKind, string> = {
  html: ".html",
  qa: ".json",
  bundle: ".zip",
  translatedPdf: ".pdf",
};

async function readJson<T>(filename: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(filename, "utf8")) as T;
  } catch {
    return null;
  }
}

async function readManifest(root: string): Promise<Manifest | null> {
  for (const filename of MANIFEST_FILENAMES) {
    const manifest = await readJson<Manifest>(path.join(root, "work", filename));
    if (manifest?.paper?.title && manifest.jobId) return manifest;
  }
  return null;
}

function defaultArtifact(slug: string, kind: ArtifactKind): string | null {
  if (kind === "html") return "html/index.html";
  if (kind === "qa") return "html/qa.json";
  if (kind === "bundle") return `${slug}-html.zip`;
  return null;
}

/** Resolve a manifest-owned artifact without allowing absolute paths, traversal, or symlink escape. */
export async function resolvePaperArtifact(
  root: string,
  candidate: string | undefined,
  fallback: string | null,
  kind: ArtifactKind,
): Promise<string | null> {
  const relative = candidate?.trim() || fallback;
  if (!relative || path.isAbsolute(relative) || path.extname(relative).toLowerCase() !== ARTIFACT_EXTENSIONS[kind]) {
    return null;
  }
  const resolvedRoot = path.resolve(root);
  const resolved = path.resolve(resolvedRoot, relative);
  if (resolved === resolvedRoot || !resolved.startsWith(`${resolvedRoot}${path.sep}`)) return null;
  try {
    const [canonicalRoot, canonicalArtifact, artifactStat] = await Promise.all([
      realpath(resolvedRoot),
      realpath(resolved),
      stat(resolved),
    ]);
    if (!artifactStat.isFile()) return null;
    if (
      canonicalArtifact === canonicalRoot
      || !canonicalArtifact.startsWith(`${canonicalRoot}${path.sep}`)
    ) return null;
    return resolved;
  } catch {
    return null;
  }
}

async function artifactPaths(root: string, slug: string, manifest: Manifest) {
  const entries = await Promise.all(
    (["html", "qa", "bundle", "translatedPdf"] as const).map(async (kind) => [
      kind,
      await resolvePaperArtifact(
        root,
        manifest.artifacts?.[kind],
        defaultArtifact(slug, kind),
        kind,
      ),
    ] as const),
  );
  return Object.fromEntries(entries) as Record<ArtifactKind, string | null>;
}

function artifactRoute(slug: string, root: string, htmlArtifact: string | null): string | null {
  if (!htmlArtifact) return null;
  const publicationRoot = path.join(root, "html");
  const relative = path.relative(publicationRoot, htmlArtifact);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) return null;
  const encoded = relative.split(path.sep).map(encodeURIComponent).join("/");
  return `/api/artifacts/${encodeURIComponent(slug)}/${encoded}`;
}

export async function findPaperArtifact(
  slug: string,
  kind: "bundle" | "translatedPdf",
): Promise<string | null> {
  if (!JOB_ID.test(slug)) return null;
  const root = path.join(OUTPUT_ROOT, slug);
  const manifest = await readManifest(root);
  if (!manifest || manifest.status !== "completed") return null;
  return resolvePaperArtifact(
    root,
    manifest.artifacts?.[kind],
    defaultArtifact(slug, kind),
    kind,
  );
}

async function loadMetadata(): Promise<LibraryMetadata> {
  const value = await readJson<LibraryMetadata>(LIBRARY_METADATA);
  if (!value || value.version !== 1 || typeof value.papers !== "object") {
    return { version: 1, papers: {} };
  }
  return value;
}

export function normalizeTags(tags: unknown): string[] {
  if (!Array.isArray(tags)) throw new Error("tags must be an array");
  const normalized: string[] = [];
  const seen = new Set<string>();
  for (const value of tags) {
    if (typeof value !== "string") throw new Error("every tag must be a string");
    const tag = value.trim().replace(/\s+/g, " ");
    if (!tag) continue;
    if (tag.length > 32) throw new Error("tags must be 32 characters or fewer");
    const key = tag.toLocaleLowerCase("ja");
    if (!seen.has(key)) {
      normalized.push(tag);
      seen.add(key);
    }
  }
  if (normalized.length > 12) throw new Error("a paper can have at most 12 tags");
  return normalized;
}

function currentPaperState(metadata: LibraryMetadata, slug: string): PaperLibraryState {
  const current = metadata.papers[slug];
  return {
    tags: normalizeTags(current?.tags ?? []),
    isRead: current?.isRead === true,
    favorite: current?.favorite === true,
  };
}

export async function savePaperLibraryState(
  slug: string,
  patch: PaperLibraryPatch,
): Promise<PaperLibraryState> {
  if (!JOB_ID.test(slug)) throw new Error("invalid paper slug");
  const metadata = await loadMetadata();
  const current = currentPaperState(metadata, slug);
  if (patch.tags !== undefined) current.tags = normalizeTags(patch.tags);
  if (patch.isRead !== undefined) {
    if (typeof patch.isRead !== "boolean") throw new Error("isRead must be a boolean");
    current.isRead = patch.isRead;
  }
  if (patch.favorite !== undefined) {
    if (typeof patch.favorite !== "boolean") throw new Error("favorite must be a boolean");
    current.favorite = patch.favorite;
  }
  metadata.papers[slug] = current;
  const temporary = path.join(DATA_ROOT, `.library-${process.pid}-${Date.now()}.tmp`);
  await mkdir(DATA_ROOT, { recursive: true });
  await writeFile(temporary, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
  await rename(temporary, LIBRARY_METADATA);
  return current;
}

export async function savePaperTags(slug: string, tags: unknown): Promise<string[]> {
  return (await savePaperLibraryState(slug, { tags })).tags;
}

function normalizeStatus(status: string | undefined): PaperStatus {
  if (
    status === "prepared" ||
    status === "translating" ||
    status === "ready_to_finalize" ||
    status === "completed" ||
    status === "needs_review" ||
    status === "failed"
  ) return status;
  return "needs_review";
}

function normalizeTargetLanguage(targetLanguage: string | undefined): "ja" {
  return targetLanguage === "ja" ? targetLanguage : "ja";
}

function displayTitle(value: string): string {
  return value
    .replace(/\s*¯\s*\\overline\{\\hbox\{\{[^{}]+\}\}\}/g, "")
    .replace(/\s+([:;,])/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function decodeHtmlAttribute(value: string): string {
  return value
    .replace(/&#(\d+);/g, (_match, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_match, code: string) => String.fromCodePoint(Number.parseInt(code, 16)))
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">");
}

function metaAttribute(tag: string, name: string): string | null {
  const pattern = new RegExp(`${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, "i");
  const match = pattern.exec(tag);
  return match ? decodeHtmlAttribute(match[1] ?? match[2] ?? match[3] ?? "") : null;
}

function normalizePublishedAt(value: string | null): string | null {
  if (!value) return null;
  const normalized = value.trim().replace(/\//g, "-");
  const dateOnly = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(normalized);
  if (dateOnly) {
    return new Date(Date.UTC(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))).toISOString();
  }
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null;
}

async function readSourceMetadata(root: string): Promise<{ authors: string[]; publishedAt: string | null }> {
  try {
    const source = await readFile(path.join(root, "work", "source.html"), "utf8");
    const authors: string[] = [];
    let publishedAt: string | null = null;
    for (const match of source.matchAll(/<meta\b[^>]*>/gi)) {
      const tag = match[0];
      const name = metaAttribute(tag, "name")?.toLowerCase();
      const content = metaAttribute(tag, "content")?.trim();
      if (!content) continue;
      if (name === "citation_author" && !authors.includes(content)) authors.push(content);
      if (
        !publishedAt &&
        ["citation_date", "citation_publication_date", "citation_online_date", "dc.date"].includes(name ?? "")
      ) publishedAt = normalizePublishedAt(content);
    }

    if (authors.length === 0) {
      for (const match of source.matchAll(
        /<span\b[^>]*class=["'][^"']*\bltx_personname\b[^"']*["'][^>]*>([\s\S]*?)<\/span>\s*(?=<span\b[^>]*class=["'][^"']*\bltx_author_notes\b|<span\b[^>]*class=["'][^"']*\bltx_author_before\b|<\/div>)/gi,
      )) {
        const body = match[1];
        const styledNames = [...body.matchAll(
          /<span\b[^>]*class=["'][^"']*\bltx_text\b[^"']*["'][^>]*>([\s\S]*?)<\/span>/gi,
        )].map((nameMatch) => displayTitle(decodeHtmlAttribute(nameMatch[1].replace(/<[^>]+>/g, " "))));
        const candidates = styledNames.length > 0
          ? styledNames
          : [displayTitle(decodeHtmlAttribute(body.split(/<span\b[^>]*class=["'][^"']*\bltx_note\b/i, 1)[0].replace(/<[^>]+>/g, " ")))];
        for (const candidate of candidates) {
          if (!candidate || candidate.startsWith("[")) continue;
          const separated = candidate.replace(/(?<=\p{Ll})(?=\p{Lu})/gu, ",");
          for (const author of separated.split(/\s*,\s*/)) {
            if (author && !authors.includes(author)) authors.push(author);
          }
        }
      }
    }

    if (!publishedAt) {
      const watermark = /<[^>]*id=["']watermark-tr["'][^>]*>([\s\S]*?)<\/[^>]+>/i.exec(source);
      const watermarkText = watermark
        ? decodeHtmlAttribute(watermark[1].replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ")
        : "";
      const arxivDate = /\b(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})\b/.exec(watermarkText)?.[1] ?? null;
      publishedAt = normalizePublishedAt(arxivDate);
    }
    return { authors, publishedAt };
  } catch {
    return { authors: [], publishedAt: null };
  }
}

export async function scanPaperLibrary(): Promise<PaperSummary[]> {
  const metadata = await loadMetadata();
  let entries;
  try {
    entries = await readdir(OUTPUT_ROOT, { withFileTypes: true });
  } catch {
    return [];
  }

  const papers = await Promise.all(
    entries
      .filter((entry) => entry.isDirectory() && JOB_ID.test(entry.name))
      .map(async (entry): Promise<PaperSummary | null> => {
        const root = path.join(OUTPUT_ROOT, entry.name);
        const manifest = await readManifest(root);
        const paper = manifest?.paper;
        if (!manifest || !paper?.title) return null;
        const artifacts = await artifactPaths(root, entry.name, manifest);
        const qa = artifacts.qa ? await readJson<QaDocument>(artifacts.qa) : null;
        const sourceMetadata = await readSourceMetadata(root);
        const libraryState = currentPaperState(metadata, entry.name);
        const chunks = manifest.chunks ?? [];
        const completed = chunks.filter((chunk) => chunk.status === "completed").length;
        const hasArtifact = manifest.status === "completed" && artifacts.html !== null && qa !== null;
        const htmlRoute = hasArtifact ? artifactRoute(entry.name, root, artifacts.html) : null;
        return {
          slug: entry.name,
          sourceType: manifest.sourceType ?? (paper.resolvedArxivId ? "arxiv" : "unknown"),
          title: displayTitle(paper.title),
          authors: paper.authors?.length ? paper.authors : sourceMetadata.authors,
          publishedAt: normalizePublishedAt(paper.publishedAt ?? null) ?? sourceMetadata.publishedAt,
          requestedArxivId: paper.requestedArxivId ?? "",
          resolvedArxivId: paper.resolvedArxivId ?? paper.requestedArxivId ?? "",
          sourceUrl: paper.sourceUrl ?? "",
          provider: manifest.provider ?? "unknown",
          targetLanguage: normalizeTargetLanguage(manifest.settings?.targetLanguage),
          status: normalizeStatus(manifest.status),
          progress: { completed, total: chunks.length },
          tags: libraryState.tags,
          isRead: libraryState.isRead,
          favorite: libraryState.favorite,
          updatedAt: manifest.updatedAt ?? manifest.createdAt ?? new Date(0).toISOString(),
          createdAt: manifest.createdAt ?? manifest.updatedAt ?? new Date(0).toISOString(),
          finalizedAt: manifest.finalizedAt ?? null,
          artifactUrl: htmlRoute,
          downloadUrl: hasArtifact && artifacts.bundle ? `/api/papers/${entry.name}/download` : null,
          qa: {
            status: qa?.status === "passed" ? "passed" : qa ? "failed" : "missing",
            figures: qa?.output?.figures ?? 0,
            tables: qa?.output?.tables ?? 0,
            math: qa?.output?.visibleMath ?? qa?.output?.math ?? 0,
            bibliographyEntries: qa?.output?.bibliographyEntries ?? 0,
            unresolvedInternalLinks: qa?.unresolvedInternalLinks ?? 0,
            missingLocalAssets: qa?.missingLocalAssets?.length ?? 0,
            browserChecked: qa?.browserDom !== undefined,
          },
        };
      }),
  );

  return papers
    .filter((paper): paper is PaperSummary => paper !== null)
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
}

export async function findPaper(slug: string): Promise<PaperSummary | null> {
  if (!JOB_ID.test(slug)) return null;
  return (await scanPaperLibrary()).find((paper) => paper.slug === slug) ?? null;
}
