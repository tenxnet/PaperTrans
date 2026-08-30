import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { constants } from "node:fs";
import { access, mkdir, open, readdir, readFile, rmdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import {
  getPaperTransRuntimeConfig,
  inspectPaperTransRuntime,
  paperTransChildEnvironment,
} from "@/lib/runtime-config";

export const runtime = "nodejs";

const MAX_PDF_BYTES = 50 * 1024 * 1024;
const MAX_MULTIPART_BYTES = MAX_PDF_BYTES + 1024 * 1024;

function slugify(filename: string) {
  const stem = filename.replace(/\.pdf$/i, "").toLowerCase();
  const normalized = stem.normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return (normalized || `paper-${Date.now()}`).slice(0, 56);
}

async function existingDigest(dataRoot: string, digest: string) {
  try {
    for (const entry of await readdir(dataRoot, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const source = path.join(dataRoot, entry.name, "source.pdf");
      try {
        const candidate = createHash("sha256").update(await readFile(source)).digest("hex");
        if (candidate === digest) return entry.name;
      } catch { /* incomplete import */ }
    }
  } catch { /* no library yet */ }
  return null;
}

function hasErrorCode(error: unknown, code: string) {
  return error instanceof Error && "code" in error && error.code === code;
}

async function reserveImportDirectories(
  dataRoot: string,
  outputRoot: string,
  baseSlug: string,
  digest: string,
) {
  await Promise.all([
    mkdir(dataRoot, { recursive: true }),
    mkdir(outputRoot, { recursive: true }),
  ]);
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const suffix = attempt === 0
      ? ""
      : attempt === 1
        ? `-${digest.slice(0, 8)}`
        : `-${digest.slice(0, 8)}-${attempt}`;
    const slug = `${baseSlug}${suffix}`;
    const paperDir = path.join(dataRoot, slug);
    const outputDir = path.join(outputRoot, slug);
    try {
      await mkdir(paperDir);
    } catch (error) {
      if (hasErrorCode(error, "EEXIST")) continue;
      throw error;
    }
    try {
      await mkdir(outputDir);
      return { slug, paperDir, outputDir };
    } catch (error) {
      await rmdir(paperDir).catch(() => undefined);
      if (hasErrorCode(error, "EEXIST")) continue;
      throw error;
    }
  }
  throw new Error("could not reserve a unique PDF job ID");
}

async function rollbackReservedImport(paperDir: string, outputDir: string) {
  await unlink(path.join(paperDir, "source.pdf")).catch(() => undefined);
  await unlink(path.join(outputDir, "job.log")).catch(() => undefined);
  await rmdir(paperDir).catch(() => undefined);
  await rmdir(outputDir).catch(() => undefined);
}

export async function POST(request: Request) {
  const config = getPaperTransRuntimeConfig();
  const runtimeReadiness = await inspectPaperTransRuntime(config);
  if (!runtimeReadiness.pdfImportReady) {
    return NextResponse.json(
      {
        code: "worker_unavailable",
        error: "PDF解析環境の準備が完了していません。PaperTransを再起動してください",
      },
      { status: 503 },
    );
  }

  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_MULTIPART_BYTES) {
    return NextResponse.json({ code: "pdf_too_large", error: "PDFは50MB以下にしてください" }, { status: 413 });
  }
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ code: "invalid_form", error: "PDFの送信内容を確認できません" }, { status: 400 });
  }
  const paper = form.get("paper");
  if (!(paper instanceof File) || (!paper.name.toLowerCase().endsWith(".pdf") && paper.type !== "application/pdf")) {
    return NextResponse.json({ code: "invalid_pdf", error: "PDFを選択してください" }, { status: 400 });
  }
  if (paper.size > MAX_PDF_BYTES) {
    return NextResponse.json({ code: "pdf_too_large", error: "PDFは50MB以下にしてください" }, { status: 413 });
  }
  const bytes = Buffer.from(await paper.arrayBuffer());
  if (bytes.length < 5 || bytes.subarray(0, 5).toString() !== "%PDF-") {
    return NextResponse.json({ code: "invalid_pdf", error: "PDFヘッダーを確認できません" }, { status: 400 });
  }

  const dataRoot = path.join(config.dataRoot, "papers");
  const digest = createHash("sha256").update(bytes).digest("hex");
  const duplicate = await existingDigest(dataRoot, digest);
  if (duplicate) {
    return NextResponse.json(
      {
        code: "duplicate_pdf",
        error: `同一PDFは取込済みです: ${duplicate}`,
        existingJobId: duplicate,
        jobId: duplicate,
        slug: duplicate,
        status: "existing",
        sourceType: "pdf",
      },
      { status: 409 },
    );
  }

  const executable = config.cliPath;
  try {
    await access(executable, constants.X_OK);
  } catch {
    return NextResponse.json(
      { code: "worker_unavailable", error: "PDF解析ワーカーを起動できません。ローカル環境を確認してください" },
      { status: 503 },
    );
  }

  const outputRoot = config.outputRoot;
  let reservation: Awaited<ReturnType<typeof reserveImportDirectories>>;
  try {
    reservation = await reserveImportDirectories(dataRoot, outputRoot, slugify(paper.name), digest);
  } catch {
    return NextResponse.json(
      { code: "job_reservation_failed", error: "PDFジョブの保存先を確保できませんでした" },
      { status: 500 },
    );
  }
  const { slug, paperDir, outputDir } = reservation;
  let log: Awaited<ReturnType<typeof open>> | null = null;
  try {
    await writeFile(path.join(paperDir, "source.pdf"), bytes, { flag: "wx" });
    log = await open(path.join(outputDir, "job.log"), "a");
    const child = spawn(
      executable,
      [
        "semantic-pipeline",
        path.join(paperDir, "source.pdf"),
        "--slug",
        slug,
        "--repo-root",
        config.repoRoot,
        "--output-root",
        config.outputRoot,
        "--structure-mode",
        "hybrid",
        "--layout-parser",
        "docling",
        "--prepare-for-mcp",
      ],
      {
        cwd: config.repoRoot,
        detached: true,
        env: paperTransChildEnvironment(config),
        stdio: ["ignore", log.fd, log.fd],
      },
    );
    await new Promise<void>((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    child.unref();
  } catch {
    await log?.close().catch(() => undefined);
    await rollbackReservedImport(paperDir, outputDir);
    return NextResponse.json(
      { code: "worker_start_failed", error: "PDF解析ワーカーの起動に失敗しました" },
      { status: 500 },
    );
  }
  await log.close();
  return NextResponse.json(
    { jobId: slug, slug, status: "preparing", sourceType: "pdf" },
    { status: 202 },
  );
}
