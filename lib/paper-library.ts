import { mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
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
  title: string;
  requestedArxivId: string;
  resolvedArxivId: string;
  sourceUrl: string;
  provider: string;
  status: PaperStatus;
  progress: { completed: number; total: number };
  tags: string[];
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
  jobId?: string;
  status?: string;
  provider?: string;
  paper?: {
    requestedArxivId?: string;
    resolvedArxivId?: string;
    title?: string;
    sourceUrl?: string;
  };
  chunks?: Array<{ status?: string }>;
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
    bibliographyEntries?: number;
  };
  unresolvedInternalLinks?: number;
  missingLocalAssets?: unknown[];
  browserDom?: unknown;
};

type LibraryMetadata = {
  version: 1;
  papers: Record<string, { tags: string[] }>;
};

const JOB_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const REPO_ROOT = process.cwd();
const OUTPUT_ROOT = path.join(REPO_ROOT, "output");
const DATA_ROOT = path.join(REPO_ROOT, "data");
const LIBRARY_METADATA = path.join(DATA_ROOT, "library.json");

async function readJson<T>(filename: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(filename, "utf8")) as T;
  } catch {
    return null;
  }
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

export async function savePaperTags(slug: string, tags: unknown): Promise<string[]> {
  if (!JOB_ID.test(slug)) throw new Error("invalid paper slug");
  const normalized = normalizeTags(tags);
  const metadata = await loadMetadata();
  metadata.papers[slug] = { tags: normalized };
  const temporary = path.join(DATA_ROOT, `.library-${process.pid}-${Date.now()}.tmp`);
  await mkdir(DATA_ROOT, { recursive: true });
  await writeFile(temporary, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
  await rename(temporary, LIBRARY_METADATA);
  return normalized;
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

function displayTitle(value: string): string {
  return value
    .replace(/\s*¯\s*\\overline\{\\hbox\{\{[^{}]+\}\}\}/g, "")
    .replace(/\s+([:;,])/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
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
        const manifest = await readJson<Manifest>(path.join(root, "work", "chatgpt-job.json"));
        if (!manifest?.paper?.title || !manifest.jobId) return null;
        const qa = await readJson<QaDocument>(path.join(root, "html", "qa.json"));
        const chunks = manifest.chunks ?? [];
        const completed = chunks.filter((chunk) => chunk.status === "completed").length;
        const hasArtifact = manifest.status === "completed" && qa !== null;
        return {
          slug: entry.name,
          title: displayTitle(manifest.paper.title),
          requestedArxivId: manifest.paper.requestedArxivId ?? "",
          resolvedArxivId: manifest.paper.resolvedArxivId ?? manifest.paper.requestedArxivId ?? "",
          sourceUrl: manifest.paper.sourceUrl ?? "",
          provider: manifest.provider ?? "unknown",
          status: normalizeStatus(manifest.status),
          progress: { completed, total: chunks.length },
          tags: normalizeTags(metadata.papers[entry.name]?.tags ?? []),
          updatedAt: manifest.updatedAt ?? manifest.createdAt ?? new Date(0).toISOString(),
          createdAt: manifest.createdAt ?? manifest.updatedAt ?? new Date(0).toISOString(),
          finalizedAt: manifest.finalizedAt ?? null,
          artifactUrl: hasArtifact ? `/api/artifacts/${entry.name}/index.html` : null,
          downloadUrl: hasArtifact ? `/api/papers/${entry.name}/download` : null,
          qa: {
            status: qa?.status === "passed" ? "passed" : qa ? "failed" : "missing",
            figures: qa?.output?.figures ?? 0,
            tables: qa?.output?.tables ?? 0,
            math: qa?.output?.visibleMath ?? 0,
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
