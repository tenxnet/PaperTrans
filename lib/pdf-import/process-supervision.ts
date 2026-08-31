import type { ChildProcess } from "node:child_process";
import {
  releasePdfImportAdmission,
  releasePersistentPdfImportAdmission,
  type PdfImportAdmission,
  type PersistentPdfImportAdmission,
} from "@/lib/pdf-import/admission";

// Docling's worker limit is 15 minutes; reserve another five minutes for the
// surrounding semantic pipeline while preventing a wedged import from running forever.
const PDF_IMPORT_OUTER_DEADLINE_MS = 20 * 60_000;
const PDF_IMPORT_TERMINATION_GRACE_MS = 5_000;

type PdfImportTerminationSignal = "SIGTERM" | "SIGKILL";

function hasErrorCode(error: unknown, code: string) {
  return error instanceof Error && "code" in error && error.code === code;
}

export function signalPdfImportProcessGroup(
  child: ChildProcess,
  signal: PdfImportTerminationSignal,
) {
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

export function preparePdfImportChildSupervision(
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
