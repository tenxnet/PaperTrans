import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { NextResponse } from "next/server";
import { normalizeArxivId } from "@/lib/arxiv-id";
import { validateLocalMutationRequest } from "@/lib/local-http-boundary";
import {
  getPaperTransRuntimeConfig,
  inspectPaperTransRuntime,
  paperTransChildEnvironment,
} from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);
const activeJobs = new Set<string>();

export async function POST(request: Request) {
  const boundary = validateLocalMutationRequest(request, ["application/json"]);
  if (!boundary.ok) {
    return NextResponse.json(
      { code: boundary.code, error: boundary.error },
      { status: boundary.status },
    );
  }
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

  const jobId = `arxiv-${arxivId.replace("/", "-")}-mcp`;
  if (activeJobs.has(jobId)) {
    return NextResponse.json({ error: "この論文のジョブを準備中です", jobId }, { status: 409 });
  }

  // Claim synchronously before runtime inspection yields; otherwise two
  // requests can both pass the Set check and spawn duplicate preparations.
  activeJobs.add(jobId);
  try {
    const config = getPaperTransRuntimeConfig();
    const readiness = await inspectPaperTransRuntime(config);
    if (!config.configurationReady || !readiness.cliReady) {
      return NextResponse.json(
        { error: "PaperTransの実行環境が準備できていません" },
        { status: 503 },
      );
    }
    const { stdout } = await execFileAsync(
      config.cliPath,
      [
        "prepare-mcp-job",
        arxivId,
        "--job-id",
        jobId,
        "--repo-root",
        config.repoRoot,
        "--output-root",
        config.outputRoot,
      ],
      {
        cwd: config.repoRoot,
        env: paperTransChildEnvironment(config),
        timeout: 180_000,
        maxBuffer: 1024 * 1024,
      },
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
