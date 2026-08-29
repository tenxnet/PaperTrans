import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);
const activeJobs = new Set<string>();

function normalizeArxivId(value: unknown) {
  if (typeof value !== "string") return null;
  return value.trim().match(/(?:arxiv:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)/i)?.[1] ?? null;
}

function resolvedRoot(configured: string | undefined, fallback: string) {
  if (!configured?.trim()) return fallback;
  return path.resolve(process.cwd(), configured.trim());
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "JSONリクエストを確認してください" }, { status: 400 });
  }
  const arxivId = normalizeArxivId(
    typeof body === "object" && body !== null && "arxivId" in body
      ? (body as { arxivId: unknown }).arxivId
      : null,
  );
  if (!arxivId) {
    return NextResponse.json({ error: "有効なarXiv IDまたはURLを入力してください" }, { status: 400 });
  }

  const jobId = `arxiv-${arxivId.toLowerCase()}-mcp`;
  if (activeJobs.has(jobId)) {
    return NextResponse.json({ error: "この論文のジョブを準備中です", jobId }, { status: 409 });
  }

  const repoRoot = resolvedRoot(process.env.PAPERTRANS_REPO_ROOT, process.cwd());
  const outputRoot = resolvedRoot(
    process.env.PAPERTRANS_OUTPUT_ROOT,
    path.join(repoRoot, "output"),
  );
  const papertrans = path.join(repoRoot, ".venv", "bin", "papertrans");
  activeJobs.add(jobId);
  try {
    const { stdout } = await execFileAsync(
      papertrans,
      [
        "prepare-mcp-job",
        arxivId,
        "--job-id",
        jobId,
        "--repo-root",
        repoRoot,
        "--output-root",
        outputRoot,
      ],
      { cwd: repoRoot, timeout: 180_000, maxBuffer: 1024 * 1024 },
    );
    return NextResponse.json(JSON.parse(stdout.trim()), { status: 201 });
  } catch (error) {
    console.error("PaperTrans MCP job preparation failed", error);
    return NextResponse.json(
      { error: "公式arXiv HTMLの取得またはジョブ準備に失敗しました。サーバーログを確認してください。" },
      { status: 500 },
    );
  } finally {
    activeJobs.delete(jobId);
  }
}
