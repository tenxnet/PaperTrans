import { randomUUID } from "node:crypto";
import {
  mkdir,
  lstat,
  open,
  opendir,
  rename,
  rmdir,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

const PDF_IMPORT_LOCK_SETUP_STALE_MS = 2 * 60_000;
const PDF_IMPORT_LOCK_FILENAME = ".papertrans-pdf-import.lock";
const PDF_IMPORT_MUTATION_GUARD_FILENAME = `${PDF_IMPORT_LOCK_FILENAME}.migration-guard`;
const PDF_IMPORT_OWNER_ENTRY_PATTERN = /^([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.(json|tmp)$/i;

export type PdfImportAdmission = symbol;

export type PersistentPdfImportAdmission = Readonly<{
  lockPath: string;
  owner: string;
  createdAt: number;
}>;

type PdfImportLockRecord = Readonly<{
  owner: string;
  createdAt: number;
  pid: number | null;
}>;

let activePdfImportAdmission: PdfImportAdmission | null = null;

export function claimPdfImportAdmission(): PdfImportAdmission | null {
  if (activePdfImportAdmission !== null) return null;
  const admission = Symbol("pdf-import");
  activePdfImportAdmission = admission;
  return admission;
}

export function releasePdfImportAdmission(admission: PdfImportAdmission) {
  if (activePdfImportAdmission === admission) activePdfImportAdmission = null;
}

function hasErrorCode(error: unknown, code: string) {
  return error instanceof Error && "code" in error && error.code === code;
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

function recordedProcessIsAlive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return !hasErrorCode(error, "ESRCH");
  }
}

function pdfImportLockRecordIsActive(record: PdfImportLockRecord) {
  if (record.pid === null) {
    return Date.now() - record.createdAt < PDF_IMPORT_LOCK_SETUP_STALE_MS;
  }
  return recordedPdfImportProcessGroupIsAlive(record.pid);
}

async function readPdfImportLock(lockPath: string): Promise<PdfImportLockRecord | null> {
  const readRecord = async (recordPath: string): Promise<PdfImportLockRecord | null> => {
    try {
      const handle = await open(recordPath, "r");
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
  };

  try {
    const info = await lstat(lockPath);
    // Read the pre-v0.2 single-file format so an upgrade still observes or
    // reclaims a worker lock left by the previous Web process.
    if (info.isFile()) return await readRecord(lockPath);
    if (!info.isDirectory()) return null;

    const directory = await opendir(lockPath);
    let record: PdfImportLockRecord | null = null;
    let entryCount = 0;
    try {
      for await (const entry of directory) {
        entryCount += 1;
        if (entryCount > 8) return null;
        if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
        const candidate = await readRecord(path.join(lockPath, entry.name));
        if (candidate === null || entry.name !== `${candidate.owner}.json` || record !== null) {
          return null;
        }
        record = candidate;
      }
    } finally {
      await directory.close().catch(() => undefined);
    }
    return record;
  } catch {
    return null;
  }
}

function pdfImportOwnerRecordPath(admission: PersistentPdfImportAdmission) {
  return path.join(admission.lockPath, `${admission.owner}.json`);
}

async function removeOwnedPdfImportLockDirectory(
  admission: PersistentPdfImportAdmission,
): Promise<boolean> {
  try {
    await unlink(pdfImportOwnerRecordPath(admission));
  } catch (error) {
    if (!hasErrorCode(error, "ENOENT") && !hasErrorCode(error, "ENOTDIR")) throw error;
    return false;
  }

  // A crash during an atomic record update can leave this owner-specific
  // temporary file behind. Its UUID makes removing it safe across replacement.
  await unlink(path.join(admission.lockPath, `${admission.owner}.tmp`)).catch((error: unknown) => {
    if (!hasErrorCode(error, "ENOENT") && !hasErrorCode(error, "ENOTDIR")) throw error;
  });

  try {
    await rmdir(admission.lockPath);
    return true;
  } catch (error) {
    if (
      hasErrorCode(error, "ENOENT")
      || hasErrorCode(error, "ENOTEMPTY")
      || hasErrorCode(error, "EEXIST")
      || hasErrorCode(error, "ENOTDIR")
    ) {
      return false;
    }
    throw error;
  }
}

async function removeUnknownPdfImportLockDirectory(lockPath: string): Promise<boolean> {
  let directory;
  try {
    directory = await opendir(lockPath);
  } catch (error) {
    if (hasErrorCode(error, "ENOENT") || hasErrorCode(error, "ENOTDIR")) return false;
    throw error;
  }

  const entryNames: string[] = [];
  let owner: string | null = null;
  try {
    for await (const entry of directory) {
      const match = entry.isFile() ? PDF_IMPORT_OWNER_ENTRY_PATTERN.exec(entry.name) : null;
      if (match === null || (owner !== null && owner !== match[1])) return false;
      owner = match[1];
      entryNames.push(entry.name);
      if (entryNames.length > 2) return false;
    }
  } finally {
    await directory.close().catch(() => undefined);
  }

  for (const entryName of entryNames) {
    try {
      // UUID-named remnants cannot select a record belonging to a replacement
      // owner. If the directory changed, these unlinks harmlessly see ENOENT.
      await unlink(path.join(lockPath, entryName));
    } catch (error) {
      if (hasErrorCode(error, "ENOENT") || hasErrorCode(error, "ENOTDIR")) return false;
      throw error;
    }
  }

  try {
    await rmdir(lockPath);
    return true;
  } catch (error) {
    if (
      hasErrorCode(error, "ENOENT")
      || hasErrorCode(error, "ENOTEMPTY")
      || hasErrorCode(error, "EEXIST")
      || hasErrorCode(error, "ENOTDIR")
    ) {
      return false;
    }
    throw error;
  }
}

async function createPdfImportLock(
  admission: PersistentPdfImportAdmission,
  pid: number | null = null,
) {
  const value: PdfImportLockRecord = {
    owner: admission.owner,
    createdAt: admission.createdAt,
    pid,
  };
  await writeFile(pdfImportOwnerRecordPath(admission), `${JSON.stringify(value)}\n`, {
    encoding: "utf8",
    flag: "wx",
    mode: 0o600,
  });
}

async function claimPdfImportMutationGuard(
  outputRoot: string,
): Promise<PersistentPdfImportAdmission | null> {
  const guard = {
    lockPath: path.join(outputRoot, PDF_IMPORT_MUTATION_GUARD_FILENAME),
    owner: randomUUID(),
    createdAt: Date.now(),
  } satisfies PersistentPdfImportAdmission;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await mkdir(guard.lockPath, { mode: 0o700 });
      try {
        await createPdfImportLock(guard, process.pid);
        return guard;
      } catch (error) {
        await unlink(pdfImportOwnerRecordPath(guard)).catch((unlinkError: unknown) => {
          if (!hasErrorCode(unlinkError, "ENOENT") && !hasErrorCode(unlinkError, "ENOTDIR")) {
            throw unlinkError;
          }
        });
        await rmdir(guard.lockPath).catch(() => undefined);
        throw error;
      }
    } catch (error) {
      if (!hasErrorCode(error, "EEXIST")) throw error;
    }

    const existing = await readPdfImportLock(guard.lockPath);
    const unknownGuardIsRecent = existing === null
      && await lstat(guard.lockPath)
        .then((info) => Date.now() - info.mtimeMs < PDF_IMPORT_LOCK_SETUP_STALE_MS)
        .catch(() => true);
    if (
      unknownGuardIsRecent
      || (
        existing?.pid === null
        && Date.now() - existing.createdAt < PDF_IMPORT_LOCK_SETUP_STALE_MS
      )
      || (existing?.pid !== null && existing?.pid !== undefined && recordedProcessIsAlive(existing.pid))
    ) {
      return null;
    }

    const guardIsDirectory = await lstat(guard.lockPath)
      .then((info) => info.isDirectory())
      .catch(() => false);
    if (!guardIsDirectory) return null;
    const removed = existing === null
      ? await removeUnknownPdfImportLockDirectory(guard.lockPath)
      : await removeOwnedPdfImportLockDirectory({
        lockPath: guard.lockPath,
        owner: existing.owner,
        createdAt: existing.createdAt,
      });
    if (!removed) return null;
  }
  return null;
}

export async function writePdfImportLock(
  admission: PersistentPdfImportAdmission,
  pid: number | null,
) {
  const ownerRecordPath = pdfImportOwnerRecordPath(admission);
  const temporaryPath = path.join(admission.lockPath, `${admission.owner}.tmp`);
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
    // If a stale-lock reclaimer replaced the directory, our owner-specific
    // record will no longer exist there. Refuse to write into the replacement.
    const existing = await readPdfImportLock(admission.lockPath);
    if (existing?.owner !== admission.owner) {
      throw new Error("PDF import lock ownership changed before it could be updated");
    }
    await rename(temporaryPath, ownerRecordPath);
  } finally {
    await unlink(temporaryPath).catch(() => undefined);
  }
}

export async function claimPersistentPdfImportAdmission(
  outputRoot: string,
): Promise<PersistentPdfImportAdmission | null> {
  await mkdir(outputRoot, { recursive: true });
  const mutationGuard = await claimPdfImportMutationGuard(outputRoot);
  if (mutationGuard === null) return null;
  try {
    return await claimPersistentPdfImportAdmissionUnderGuard(outputRoot);
  } finally {
    await removeOwnedPdfImportLockDirectory(mutationGuard);
  }
}

async function claimPersistentPdfImportAdmissionUnderGuard(
  outputRoot: string,
): Promise<PersistentPdfImportAdmission | null> {
  const lockPath = path.join(outputRoot, PDF_IMPORT_LOCK_FILENAME);
  const admission = {
    lockPath,
    owner: randomUUID(),
    createdAt: Date.now(),
  } satisfies PersistentPdfImportAdmission;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await mkdir(lockPath, { mode: 0o700 });
      try {
        await createPdfImportLock(admission);
        return admission;
      } catch (error) {
        // This directory is not visible as a usable lock until its unique
        // owner record exists. Clean only our UUID-named record, then remove
        // the directory only if it is empty.
        await unlink(pdfImportOwnerRecordPath(admission)).catch((unlinkError: unknown) => {
          if (!hasErrorCode(unlinkError, "ENOENT") && !hasErrorCode(unlinkError, "ENOTDIR")) {
            throw unlinkError;
          }
        });
        await rmdir(lockPath).catch(() => undefined);
        throw error;
      }
    } catch (error) {
      if (!hasErrorCode(error, "EEXIST")) throw error;
    }

    const existing = await readPdfImportLock(lockPath);
    const unknownLockIsRecent = existing === null
      && await lstat(lockPath)
        .then((info) => Date.now() - info.mtimeMs < PDF_IMPORT_LOCK_SETUP_STALE_MS)
        .catch(() => true);
    if (
      unknownLockIsRecent
      || (existing !== null && pdfImportLockRecordIsActive(existing))
    ) {
      return null;
    }

    const existingLockIsDirectory = await lstat(lockPath)
      .then((info) => info.isDirectory())
      .catch(() => false);
    if (existingLockIsDirectory) {
      const removed = existing === null
        ? await removeUnknownPdfImportLockDirectory(lockPath)
        : await removeOwnedPdfImportLockDirectory({
          lockPath,
          owner: existing.owner,
          createdAt: existing.createdAt,
        });
      if (!removed) return null;
      continue;
    }

    // Legacy single-file locks cannot participate in the v2 owner-specific
    // directory protocol or its sidecar guard. Re-read after acquiring the
    // guard so an active replacement is recognized, but never mutate even a
    // stale legacy file: a concurrently running old version could replace it
    // after this check and no portable filesystem compare-and-delete exists.
    // Status reports the stale record for explicit operator migration.
    const refreshedInfo = await lstat(lockPath).catch(() => null);
    if (refreshedInfo === null) continue;
    if (!refreshedInfo.isFile()) return null;
    const refreshed = await readPdfImportLock(lockPath);
    if (refreshed === null || pdfImportLockRecordIsActive(refreshed)) return null;
    return null;
  }
  return null;
}

export async function releasePersistentPdfImportAdmission(
  admission: PersistentPdfImportAdmission,
) {
  // The owner UUID is part of the pathname. If another process moved the old
  // lock aside and created a replacement, the helper sees ENOENT rather than
  // deleting the replacement owner's record. A replacement directory also
  // cannot be removed while its own record remains inside it. No separate
  // read-before-delete ownership check is needed, eliminating that race.
  await removeOwnedPdfImportLockDirectory(admission);
}
