import { createHash, randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { constants } from "node:fs";
import { access, mkdir, open, readdir, readFile, rename, rmdir, stat, unlink, writeFile } from "node:fs/promises";
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
const PDF_IMPORT_UPLOAD_DEADLINE_MS = 60_000;
const PDF_IMPORT_LOCK_SETUP_STALE_MS = 2 * 60_000;
const PDF_IMPORT_LOCK_FILENAME = ".papertrans-pdf-import.lock";
// Docling's worker limit is 15 minutes; reserve another five minutes for the
// surrounding semantic pipeline while preventing a wedged import from running forever.
const PDF_IMPORT_OUTER_DEADLINE_MS = 20 * 60_000;
const PDF_IMPORT_TERMINATION_GRACE_MS = 5_000;

type PdfImportAdmission = symbol;

type PersistentPdfImportAdmission = Readonly<{
  lockPath: string;
  owner: string;
  createdAt: number;
}>;

type PdfImportLockRecord = Readonly<{
  owner: string;
  createdAt: number;
  pid: number | null;
}>;

class PdfImportRequestError extends Error {
  constructor(
    readonly code: "invalid_form" | "pdf_too_large" | "pdf_upload_timeout",
    readonly status: 400 | 408 | 413,
    message: string,
  ) {
    super(message);
  }
}

let activePdfImportAdmission: PdfImportAdmission | null = null;

function claimPdfImportAdmission(): PdfImportAdmission | null {
  if (activePdfImportAdmission !== null) return null;
  const admission = Symbol("pdf-import");
  activePdfImportAdmission = admission;
  return admission;
}

function releasePdfImportAdmission(admission: PdfImportAdmission) {
  if (activePdfImportAdmission === admission) activePdfImportAdmission = null;
}

function recordedPdfImportProcessGroupIsAlive(pid: number) {
  try {
    // The imported pipeline is detached, so its PID is also its POSIX process
    // group ID. Checking the group keeps a restarted Web process from
    // reclaiming the lock while an orphaned Docling descendant is still alive.
    process.kill(process.platform === "win32" ? pid : -pid, 0);
    return true;
  } catch (error) {
    return !hasErrorCode(error, "ESRCH");
  }
}

async function readPdfImportLock(lockPath: string): Promise<PdfImportLockRecord | null> {
  try {
    const handle = await open(lockPath, "r");
    try {
      const info = await handle.stat();
      if (!info.isFile() || info.size > 4096) return null;
      const value = JSON.parse(await handle.readFile({ encoding: "utf8" })) as Partial<PdfImportLockRecord>;
      if (
        typeof value.owner !== "string"
        || typeof value.createdAt !== "number"
        || !Number.isFinite(value.createdAt)
        || (value.pid !== null && (!Number.isInteger(value.pid) || Number(value.pid) <= 0))
      ) {
        return null;
      }
      return {
        owner: value.owner,
        createdAt: value.createdAt,
        pid: value.pid === null ? null : Number(value.pid),
      };
    } finally {
      await handle.close();
    }
  } catch {
    return null;
  }
}

async function writePdfImportLock(
  admission: PersistentPdfImportAdmission,
  pid: number | null,
) {
  const temporaryPath = `${admission.lockPath}.${admission.owner}.tmp`;
  const value: PdfImportLockRecord = {
    owner: admission.owner,
    createdAt: admission.createdAt,
    pid,
  };
  try {
    await writeFile(temporaryPath, `${JSON.stringify(value)}\n`, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
    await rename(temporaryPath, admission.lockPath);
  } finally {
    await unlink(temporaryPath).catch(() => undefined);
  }
}

async function claimPersistentPdfImportAdmission(
  outputRoot: string,
): Promise<PersistentPdfImportAdmission | null> {
  await mkdir(outputRoot, { recursive: true });
  const lockPath = path.join(outputRoot, PDF_IMPORT_LOCK_FILENAME);
  const admission = {
    lockPath,
    owner: randomUUID(),
    createdAt: Date.now(),
  } satisfies PersistentPdfImportAdmission;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const handle = await open(lockPath, "wx", 0o600);
      await handle.close();
      try {
        await writePdfImportLock(admission, null);
        return admission;
      } catch (error) {
        await unlink(lockPath).catch(() => undefined);
        throw error;
      }
    } catch (error) {
      if (!hasErrorCode(error, "EEXIST")) throw error;
    }

    const existing = await readPdfImportLock(lockPath);
    const unknownLockIsRecent = existing === null
      && await stat(lockPath)
        .then((info) => Date.now() - info.mtimeMs < PDF_IMPORT_LOCK_SETUP_STALE_MS)
        .catch(() => true);
    const recentlyClaimed = existing !== null
      && existing.pid === null
      && Date.now() - existing.createdAt < PDF_IMPORT_LOCK_SETUP_STALE_MS;
    if (
      unknownLockIsRecent
      ||
      recentlyClaimed
      || (
        existing?.pid !== null
        && existing?.pid !== undefined
        && recordedPdfImportProcessGroupIsAlive(existing.pid)
      )
    ) {
      return null;
    }

    const stalePath = `${lockPath}.stale-${admission.owner}`;
    try {
      await rename(lockPath, stalePath);
      await unlink(stalePath).catch(() => undefined);
    } catch (error) {
      if (!hasErrorCode(error, "ENOENT")) return null;
    }
  }
  return null;
}

async function releasePersistentPdfImportAdmission(
  admission: PersistentPdfImportAdmission,
) {
  const existing = await readPdfImportLock(admission.lockPath);
  if (existing?.owner === admission.owner) {
    await unlink(admission.lockPath).catch(() => undefined);
  }
}

async function readBoundedPdfImportForm(request: Request): Promise<FormData> {
  if (request.body === null) {
    throw new PdfImportRequestError("invalid_form", 400, "PDFの送信内容を確認できません");
  }
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  const deadline = new Promise<never>((_resolve, reject) => {
    deadlineTimer = setTimeout(() => {
      void reader.cancel("PDF upload deadline exceeded").catch(() => undefined);
      reject(new PdfImportRequestError(
        "pdf_upload_timeout",
        408,
        "PDFのアップロードが60秒を超えました",
      ));
    }, PDF_IMPORT_UPLOAD_DEADLINE_MS);
  });
  try {
    for (;;) {
      const { done, value } = await Promise.race([reader.read(), deadline]);
      if (done) break;
      received += value.byteLength;
      if (received > MAX_MULTIPART_BYTES) {
        void reader.cancel("PDF upload exceeds the byte limit").catch(() => undefined);
        throw new PdfImportRequestError("pdf_too_large", 413, "PDFは50MB以下にしてください");
      }
      chunks.push(value);
    }
  } finally {
    if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  }

  const body = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const headers = new Headers(request.headers);
  headers.set("content-length", String(received));
  headers.delete("transfer-encoding");
  try {
    return await new Request(request.url, {
      method: request.method,
      headers,
      body,
    }).formData();
  } catch {
    throw new PdfImportRequestError("invalid_form", 400, "PDFの送信内容を確認できません");
  }
}

type PdfImportTerminationSignal = "SIGTERM" | "SIGKILL";

function signalPdfImportProcessGroup(child: ChildProcess, signal: PdfImportTerminationSignal) {
  if (child.pid === undefined) return;
  try {
    if (process.platform === "win32") {
      child.kill(signal);
    } else {
      // detached:true makes the child a POSIX process-group leader.
      process.kill(-child.pid, signal);
    }
  } catch {
    // The child (or its process group) exited between the lifecycle event and signal.
  }
}

function pdfImportProcessGroupIsAlive(child: ChildProcess) {
  if (child.pid === undefined) return false;
  if (process.platform === "win32") return child.exitCode === null;
  try {
    process.kill(-child.pid, 0);
    return true;
  } catch (error) {
    return !hasErrorCode(error, "ESRCH");
  }
}

function preparePdfImportChildSupervision(
  child: ChildProcess,
  admission: PdfImportAdmission,
  persistentAdmission: PersistentPdfImportAdmission,
) {
  let admissionTransferred = false;
  let settled = false;
  let released = false;
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  let forceKillTimer: ReturnType<typeof setTimeout> | null = null;
  let processGroupPollTimer: ReturnType<typeof setTimeout> | null = null;

  const releaseAll = () => {
    if (!admissionTransferred || released) return;
    released = true;
    releasePdfImportAdmission(admission);
    void releasePersistentPdfImportAdmission(persistentAdmission);
  };

  const scheduleProcessGroupCleanup = () => {
    if (forceKillTimer !== null) return;
    signalPdfImportProcessGroup(child, "SIGTERM");
    forceKillTimer = setTimeout(() => {
      forceKillTimer = null;
      signalPdfImportProcessGroup(child, "SIGKILL");
      const releaseAfterProcessGroupExit = () => {
        processGroupPollTimer = null;
        if (!pdfImportProcessGroupIsAlive(child)) {
          releaseAll();
          return;
        }
        // Fail closed if an uninterruptible descendant remains. The unref'd
        // poll does not keep Next.js alive; after a restart the persistent
        // process-group lock continues enforcing the same admission boundary.
        processGroupPollTimer = setTimeout(releaseAfterProcessGroupExit, 100);
        processGroupPollTimer.unref();
      };
      releaseAfterProcessGroupExit();
    }, PDF_IMPORT_TERMINATION_GRACE_MS);
    forceKillTimer.unref();
  };

  const settle = () => {
    if (settled) return;
    settled = true;
    child.off("exit", settle);
    child.off("error", settle);
    if (deadlineTimer !== null) clearTimeout(deadlineTimer);
    if (forceKillTimer !== null || processGroupPollTimer !== null) return;
    if (pdfImportProcessGroupIsAlive(child)) {
      scheduleProcessGroupCleanup();
      return;
    }
    releaseAll();
  };

  child.once("exit", settle);
  child.once("error", settle);

  return () => {
    if (admissionTransferred) return;
    admissionTransferred = true;
    if (settled) {
      if (forceKillTimer === null) releaseAll();
      return;
    }
    deadlineTimer = setTimeout(() => {
      deadlineTimer = null;
      scheduleProcessGroupCleanup();
    }, PDF_IMPORT_OUTER_DEADLINE_MS);
    deadlineTimer.unref();
  };
}

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
