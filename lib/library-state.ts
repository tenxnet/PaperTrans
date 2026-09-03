import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import {
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  rename,
  rmdir,
  unlink,
} from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const JOB_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const LIBRARY_METADATA_MAX_BYTES = 8 * 1024 * 1024;
const LIBRARY_LOCK_STALE_MS = 30_000;
const LIBRARY_LOCK_WAIT_MS = 5_000;
const LIBRARY_LOCK_RETRY_MS = 20;
const processState = globalThis as typeof globalThis & {
  __papertransLibraryProcessInstance?: string;
};
const PROCESS_INSTANCE = processState.__papertransLibraryProcessInstance ?? randomUUID();
processState.__papertransLibraryProcessInstance = PROCESS_INSTANCE;
const execFileAsync = promisify(execFile);
let currentProcessStartedAt: number | undefined;

type LibraryMetadata = {
  version: 1;
  papers: Record<string, PaperLibraryState>;
};

type LibraryLock = Readonly<{
  lockPath: string;
  owner: string;
  pid: number;
  processStartedAt: number;
  processInstance: string;
}>;

type LibraryLockRecord = Readonly<{
  owner: string;
  pid: number;
  processStartedAt: number;
  processInstance: string;
  createdAt: number;
}>;

export type PaperLibraryState = {
  tags: string[];
  isRead: boolean;
  favorite: boolean;
};

export type PaperLibraryPatch = {
  tags?: unknown;
  addTags?: unknown;
  removeTags?: unknown;
  isRead?: unknown;
  favorite?: unknown;
};

export class LibraryMetadataError extends Error {}
export class LibraryMetadataBusyError extends LibraryMetadataError {}

const LIBRARY_LOCK_RECORD_PATTERN =
  /^(?<owner>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.(?<pid>[1-9][0-9]*)\.(?<started>[0-9]+)\.(?<instance>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$/i;

function hasErrorCode(error: unknown, code: string) {
  return error instanceof Error && "code" in error && error.code === code;
}

function processIsAlive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return !hasErrorCode(error, "ESRCH");
  }
}

async function inspectProcessStartedAt(pid: number): Promise<number | null> {
  // Linux exposes a stable per-boot process start tick without requiring a
  // userland `ps` binary (important for minimal and Nix-style environments).
  try {
    const stat = await readFile(`/proc/${pid}/stat`, "utf8");
    const fields = stat.slice(stat.lastIndexOf(")") + 2).trim().split(/\s+/);
    const startTicks = Number(fields[19]); // proc_pid_stat(5), field 22.
    if (Number.isSafeInteger(startTicks) && startTicks > 0) return startTicks;
  } catch {
    // macOS has no procfs; use its stable process start timestamp below.
  }
  try {
    const { stdout } = await execFileAsync(
      "/bin/ps",
      ["-o", "lstart=", "-p", String(pid)],
      {
        encoding: "utf8",
        env: { ...process.env, LANG: "C", LC_ALL: "C" },
        maxBuffer: 4096,
        timeout: 1000,
      },
    );
    const startedAt = Date.parse(stdout.trim());
    return Number.isFinite(startedAt) ? Math.floor(startedAt / 1000) : null;
  } catch {
    return null;
  }
}

function getCurrentProcessStartedAt() {
  if (currentProcessStartedAt !== undefined) return Promise.resolve(currentProcessStartedAt);
  return inspectProcessStartedAt(process.pid).then((startedAt) => {
    // A live PID without a readable start identity is safe but less available:
    // peers preserve its lock until the PID exits instead of guessing at reuse.
    if (startedAt !== null) currentProcessStartedAt = startedAt;
    return startedAt ?? 0;
  });
}

async function recordedProcessStillOwnsLock(record: LibraryLockRecord) {
  if (!processIsAlive(record.pid)) return false;
  if (record.pid === process.pid) {
    const startedAt = await getCurrentProcessStartedAt();
    return record.processStartedAt === 0
      || startedAt === 0
      || record.processStartedAt === startedAt;
  }
  if (record.processStartedAt === 0) return true;
  const startedAt = await inspectProcessStartedAt(record.pid);
  if (startedAt === null) {
    // If this host cannot inspect a live process, preserve its lock rather than
    // risk allowing two writers into the metadata critical section.
    return true;
  }
  return startedAt === record.processStartedAt;
}

function lockRecordPath(lock: LibraryLock) {
  return path.join(
    lock.lockPath,
    `${lock.owner}.${lock.pid}.${lock.processStartedAt}.${lock.processInstance}.json`,
  );
}

async function readLockRecord(lockPath: string): Promise<LibraryLockRecord | null> {
  try {
    const lockInfo = await lstat(lockPath);
    if (!lockInfo.isDirectory() || lockInfo.isSymbolicLink()) {
      throw new LibraryMetadataError("library metadata lock is not a safe directory");
    }
    const entries = await readdir(lockPath, { withFileTypes: true });
    if (entries.length === 0) return null;
    if (entries.length !== 1 || !entries[0].isFile()) {
      throw new LibraryMetadataError("library metadata lock has unsafe contents");
    }
    const filenameMatch = LIBRARY_LOCK_RECORD_PATTERN.exec(entries[0].name);
    if (!filenameMatch?.groups) {
      throw new LibraryMetadataError("library metadata lock has an invalid owner record");
    }
    const recordPath = path.join(lockPath, entries[0].name);
    const recordInfo = await lstat(recordPath);
    if (!recordInfo.isFile() || recordInfo.isSymbolicLink() || recordInfo.size > 4096) {
      throw new LibraryMetadataError("library metadata lock owner is not a safe file");
    }
    const owner = filenameMatch.groups.owner;
    const pid = Number(filenameMatch.groups.pid);
    const processStartedAt = Number(filenameMatch.groups.started);
    const processInstance = filenameMatch.groups.instance;
    if (!Number.isSafeInteger(pid) || !Number.isSafeInteger(processStartedAt)) {
      throw new LibraryMetadataError("library metadata lock has an invalid process owner");
    }
    let createdAt = recordInfo.mtimeMs;
    try {
      const value = JSON.parse(await readFile(recordPath, "utf8")) as Partial<LibraryLockRecord>;
      if (
        value.owner === owner
        && value.pid === pid
        && value.processStartedAt === processStartedAt
        && value.processInstance === processInstance
        && typeof value.createdAt === "number"
        && Number.isFinite(value.createdAt)
      ) createdAt = value.createdAt;
    } catch {
      // A crashed writer can leave a partial record. The UUID and PID encoded
      // in its safe filename still let a later process reclaim it correctly.
    }
    return { owner, pid, processStartedAt, processInstance, createdAt };
  } catch (error) {
    if (hasErrorCode(error, "ENOENT")) return null;
    throw error;
  }
}

async function removeOwnedLock(lockPath: string, record: LibraryLockRecord) {
  const lockInfo = await lstat(lockPath).catch(() => null);
  if (!lockInfo?.isDirectory() || lockInfo.isSymbolicLink()) return false;
  const recordPath = path.join(
    lockPath,
    `${record.owner}.${record.pid}.${record.processStartedAt}.${record.processInstance}.json`,
  );
  const recordInfo = await lstat(recordPath).catch(() => null);
  if (!recordInfo?.isFile() || recordInfo.isSymbolicLink()) return false;
  try {
    await unlink(recordPath);
  } catch (error) {
    if (hasErrorCode(error, "ENOENT") || hasErrorCode(error, "ENOTDIR")) return false;
    throw error;
  }
  try {
    await rmdir(lockPath);
    return true;
  } catch (error) {
    if (
      hasErrorCode(error, "ENOENT")
      || hasErrorCode(error, "ENOTEMPTY")
      || hasErrorCode(error, "ENOTDIR")
    ) return false;
    throw error;
  }
}

async function claimLibraryLock(dataRoot: string): Promise<LibraryLock> {
  const lock: LibraryLock = {
    lockPath: path.join(dataRoot, ".library.lock"),
    owner: randomUUID(),
    pid: process.pid,
    processStartedAt: await getCurrentProcessStartedAt(),
    processInstance: PROCESS_INSTANCE,
  };
  const deadline = Date.now() + LIBRARY_LOCK_WAIT_MS;

  while (true) {
    try {
      await mkdir(lock.lockPath, { mode: 0o700 });
      const record: LibraryLockRecord = {
        owner: lock.owner,
        pid: lock.pid,
        processStartedAt: lock.processStartedAt,
        processInstance: lock.processInstance,
        createdAt: Date.now(),
      };
      try {
        await open(lockRecordPath(lock), "wx", 0o600).then(async (handle) => {
          try {
            await handle.writeFile(`${JSON.stringify(record)}\n`, "utf8");
            await handle.sync();
          } finally {
            await handle.close();
          }
        });
        return lock;
      } catch (error) {
        await unlink(lockRecordPath(lock)).catch(() => undefined);
        await rmdir(lock.lockPath).catch(() => undefined);
        throw error;
      }
    } catch (error) {
      if (!hasErrorCode(error, "EEXIST")) throw error;
    }

    const existing = await readLockRecord(lock.lockPath);
    if (existing !== null && !await recordedProcessStillOwnsLock(existing)) {
      if (await removeOwnedLock(lock.lockPath, existing)) continue;
    } else if (existing === null) {
      const staleEmptyLock = await lstat(lock.lockPath)
        .then((info) => info.isDirectory() && Date.now() - info.mtimeMs > LIBRARY_LOCK_STALE_MS)
        .catch(() => false);
      if (staleEmptyLock) {
        try {
          await rmdir(lock.lockPath);
          continue;
        } catch (error) {
          if (!hasErrorCode(error, "ENOENT") && !hasErrorCode(error, "ENOTEMPTY")) throw error;
        }
      }
    }

    if (Date.now() >= deadline) {
      throw new LibraryMetadataBusyError("library metadata is busy; retry the update");
    }
    await new Promise((resolve) => setTimeout(resolve, LIBRARY_LOCK_RETRY_MS));
  }
}

async function releaseLibraryLock(lock: LibraryLock) {
  try {
    await unlink(lockRecordPath(lock));
  } catch (error) {
    if (hasErrorCode(error, "ENOENT") || hasErrorCode(error, "ENOTDIR")) return;
    throw error;
  }
  try {
    await rmdir(lock.lockPath);
  } catch (error) {
    if (!hasErrorCode(error, "ENOENT") && !hasErrorCode(error, "ENOTEMPTY")) throw error;
  }
}

async function loadMetadata(metadataPath: string): Promise<LibraryMetadata> {
  try {
    const info = await lstat(metadataPath);
    if (!info.isFile() || info.isSymbolicLink() || info.size > LIBRARY_METADATA_MAX_BYTES) {
      throw new LibraryMetadataError("library metadata is not a safe regular file");
    }
    const value = JSON.parse(await readFile(metadataPath, "utf8")) as Partial<LibraryMetadata>;
    if (
      value.version !== 1
      || value.papers === null
      || typeof value.papers !== "object"
      || Array.isArray(value.papers)
    ) {
      throw new LibraryMetadataError("library metadata has an unsupported structure");
    }
    const papers: Record<string, PaperLibraryState> = {};
    for (const [slug, state] of Object.entries(value.papers)) {
      if (
        !JOB_ID.test(slug)
        || state === null
        || typeof state !== "object"
        || Array.isArray(state)
        || !Array.isArray(state.tags)
        || (state.isRead !== undefined && typeof state.isRead !== "boolean")
        || (state.favorite !== undefined && typeof state.favorite !== "boolean")
      ) {
        throw new LibraryMetadataError("library metadata contains an invalid paper state");
      }
      let normalizedTags: string[];
      try {
        normalizedTags = normalizeTags(state.tags);
      } catch (error) {
        throw new LibraryMetadataError("library metadata contains invalid tags", { cause: error });
      }
      if (
        normalizedTags.length !== state.tags.length
        || normalizedTags.some((tag, index) => tag !== state.tags[index])
      ) {
        throw new LibraryMetadataError("library metadata contains non-normalized tags");
      }
      // Early schema-v1 files only stored tags. Preserve those files and
      // migrate the two later boolean fields explicitly on the next write.
      papers[slug] = {
        tags: normalizedTags,
        isRead: state.isRead ?? false,
        favorite: state.favorite ?? false,
      };
    }
    return { version: 1, papers };
  } catch (error) {
    if (hasErrorCode(error, "ENOENT")) return { version: 1, papers: {} };
    if (error instanceof LibraryMetadataError) throw error;
    throw new LibraryMetadataError("library metadata is corrupt; restore or remove it manually", { cause: error });
  }
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

export function currentPaperState(
  papers: Record<string, PaperLibraryState>,
  slug: string,
): PaperLibraryState {
  const current = papers[slug];
  return {
    tags: normalizeTags(current?.tags ?? []),
    isRead: current?.isRead === true,
    favorite: current?.favorite === true,
  };
}

export async function loadPaperLibraryStates(
  dataRoot: string,
): Promise<Record<string, PaperLibraryState>> {
  return (await loadMetadata(path.join(dataRoot, "library.json"))).papers;
}

export async function savePaperLibraryStateAt(
  dataRoot: string,
  slug: string,
  patch: PaperLibraryPatch,
): Promise<PaperLibraryState> {
  if (!JOB_ID.test(slug)) throw new Error("invalid paper slug");
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) {
    throw new Error("library update must be a JSON object");
  }
  const patchRecord = patch as Record<string, unknown>;
  const patchFields = ["tags", "addTags", "removeTags", "isRead", "favorite"];
  const suppliedFields = Object.keys(patchRecord);
  if (
    suppliedFields.length === 0
    || suppliedFields.some((field) => !patchFields.includes(field))
    || !patchFields.some((field) => patchRecord[field] !== undefined)
  ) {
    throw new Error("library update must contain a supported field");
  }
  await mkdir(dataRoot, { recursive: true });
  const lock = await claimLibraryLock(dataRoot);
  const metadataPath = path.join(dataRoot, "library.json");
  const temporary = path.join(dataRoot, `.library-${process.pid}-${randomUUID()}.tmp`);
  try {
    const metadata = await loadMetadata(metadataPath);
    const current = currentPaperState(metadata.papers, slug);
    if (patch.tags !== undefined && (patch.addTags !== undefined || patch.removeTags !== undefined)) {
      throw new Error("tags cannot be replaced and mutated in the same update");
    }
    if (patch.tags !== undefined) current.tags = normalizeTags(patch.tags);
    if (patch.addTags !== undefined) {
      current.tags = normalizeTags([...current.tags, ...normalizeTags(patch.addTags)]);
    }
    if (patch.removeTags !== undefined) {
      const removed = new Set(
        normalizeTags(patch.removeTags).map((tag) => tag.toLocaleLowerCase("ja")),
      );
      current.tags = current.tags.filter(
        (tag) => !removed.has(tag.toLocaleLowerCase("ja")),
      );
    }
    if (patch.isRead !== undefined) {
      if (typeof patch.isRead !== "boolean") throw new Error("isRead must be a boolean");
      current.isRead = patch.isRead;
    }
    if (patch.favorite !== undefined) {
      if (typeof patch.favorite !== "boolean") throw new Error("favorite must be a boolean");
      current.favorite = patch.favorite;
    }
    metadata.papers[slug] = current;

    const handle = await open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(`${JSON.stringify(metadata, null, 2)}\n`, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, metadataPath);
    return current;
  } finally {
    try {
      await unlink(temporary).catch((error: unknown) => {
        if (!hasErrorCode(error, "ENOENT")) throw error;
      });
    } finally {
      await releaseLibraryLock(lock);
    }
  }
}
