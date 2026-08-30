import { createHash } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { constants } from "node:fs";
import { access, mkdir, open, readdir, readFile, rmdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import {
  claimPdfImportAdmission,
  claimPersistentPdfImportAdmission,
  releasePdfImportAdmission,
  releasePersistentPdfImportAdmission,
  writePdfImportLock,
  type PdfImportAdmission,
  type PersistentPdfImportAdmission,
} from "@/lib/pdf-import/admission";
import {
  preparePdfImportChildSupervision,
  signalPdfImportProcessGroup,
} from "@/lib/pdf-import/process-supervision";
import {
  MAX_MULTIPART_BYTES,
  MAX_PDF_BYTES,
  PdfImportRequestError,
  readBoundedPdfImportForm,
} from "@/lib/pdf-import/upload";
import {
  getPaperTransRuntimeConfig,
  inspectPaperTransRuntime,
  paperTransChildEnvironment,
} from "@/lib/runtime-config";

export const runtime = "nodejs";

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

async function runPdfImport(
  request: Request,
  config: ReturnType<typeof getPaperTransRuntimeConfig>,
  admission: PdfImportAdmission,
  persistentAdmission: PersistentPdfImportAdmission,
  markAdmissionTransferred: () => void,
) {
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_MULTIPART_BYTES) {
    return NextResponse.json({ code: "pdf_too_large", error: "PDFは50MB以下にしてください" }, { status: 413 });
  }
  let form: FormData;
  try {
    form = await readBoundedPdfImportForm(request);
  } catch (error) {
    if (error instanceof PdfImportRequestError) {
      return NextResponse.json(
        { code: error.code, error: error.message },
        { status: error.status },
      );
    }
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
  let child: ChildProcess | null = null;
  let transferAdmissionToChild: (() => void) | null = null;
  try {
    await writeFile(path.join(paperDir, "source.pdf"), bytes, { flag: "wx" });
    log = await open(path.join(outputDir, "job.log"), "a");
    const spawnedChild = spawn(
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
    child = spawnedChild;
    transferAdmissionToChild = preparePdfImportChildSupervision(
      spawnedChild,
      admission,
      persistentAdmission,
    );
    await new Promise<void>((resolve, reject) => {
      const handleSpawn = () => {
        spawnedChild.off("error", handleError);
        resolve();
      };
      const handleError = (error: Error) => {
        spawnedChild.off("spawn", handleSpawn);
        reject(error);
      };
      spawnedChild.once("spawn", handleSpawn);
      spawnedChild.once("error", handleError);
    });
    if (spawnedChild.pid === undefined) throw new Error("PDF import child has no PID");
    await writePdfImportLock(persistentAdmission, spawnedChild.pid);
    transferAdmissionToChild();
    markAdmissionTransferred();
    spawnedChild.unref();
  } catch {
    if (child !== null) {
      signalPdfImportProcessGroup(child, "SIGKILL");
      if (child.pid !== undefined && transferAdmissionToChild !== null) {
        // A post-spawn setup failure must not release admission before every
        // process in the detached import group has actually exited.
        transferAdmissionToChild();
        markAdmissionTransferred();
      }
    }
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

export async function POST(request: Request) {
  // Claim before the first await so concurrent requests cannot both pass admission.
  const admission = claimPdfImportAdmission();
  if (admission === null) {
    return NextResponse.json(
      { code: "pdf_import_busy", error: "別のPDFを取り込み中です。完了後にもう一度お試しください" },
      { status: 429 },
    );
  }

  let admissionTransferred = false;
  let persistentAdmission: PersistentPdfImportAdmission | null = null;
  try {
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
    try {
      persistentAdmission = await claimPersistentPdfImportAdmission(config.outputRoot);
    } catch {
      return NextResponse.json(
        { code: "job_admission_failed", error: "PDF取込ロックを確保できませんでした" },
        { status: 500 },
      );
    }
    if (persistentAdmission === null) {
      return NextResponse.json(
        { code: "pdf_import_busy", error: "別のPDFを取り込み中です。完了後にもう一度お試しください" },
        { status: 429 },
      );
    }
    return await runPdfImport(request, config, admission, persistentAdmission, () => {
      admissionTransferred = true;
    });
  } finally {
    if (!admissionTransferred) {
      releasePdfImportAdmission(admission);
      if (persistentAdmission !== null) {
        await releasePersistentPdfImportAdmission(persistentAdmission);
      }
    }
  }
}
