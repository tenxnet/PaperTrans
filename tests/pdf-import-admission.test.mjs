import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir, mkdtemp, rename, rm, stat, utimes, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  claimPersistentPdfImportAdmission,
  releasePersistentPdfImportAdmission,
  writePdfImportLock,
} from "../lib/pdf-import/admission.ts";

async function withTemporaryOutputRoot(run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "papertrans-admission-"));
  try {
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test("an old owner cannot release a replacement lock", async () => {
  await withTemporaryOutputRoot(async (outputRoot) => {
    const oldAdmission = await claimPersistentPdfImportAdmission(outputRoot);
    assert.notEqual(oldAdmission, null);

    const displacedPath = `${oldAdmission.lockPath}.displaced`;
    await rename(oldAdmission.lockPath, displacedPath);

    const replacementAdmission = await claimPersistentPdfImportAdmission(outputRoot);
    assert.notEqual(replacementAdmission, null);
    await releasePersistentPdfImportAdmission(oldAdmission);

    assert.equal((await stat(replacementAdmission.lockPath)).isDirectory(), true);
    assert.equal(await claimPersistentPdfImportAdmission(outputRoot), null);

    await releasePersistentPdfImportAdmission(replacementAdmission);
    const nextAdmission = await claimPersistentPdfImportAdmission(outputRoot);
    assert.notEqual(nextAdmission, null);
    await releasePersistentPdfImportAdmission(nextAdmission);
  });
});

test("release removes only its owner record and makes the lock claimable", async () => {
  await withTemporaryOutputRoot(async (outputRoot) => {
    const admission = await claimPersistentPdfImportAdmission(outputRoot);
    assert.notEqual(admission, null);

    await releasePersistentPdfImportAdmission(admission);

    const nextAdmission = await claimPersistentPdfImportAdmission(outputRoot);
    assert.notEqual(nextAdmission, null);
    await releasePersistentPdfImportAdmission(nextAdmission);
  });
});

test("legacy single-file locks fail closed whether recent or stale", async () => {
  await withTemporaryOutputRoot(async (outputRoot) => {
    const lockPath = path.join(outputRoot, ".papertrans-pdf-import.lock");
    const legacyRecord = {
      owner: "legacy-owner",
      createdAt: Date.now(),
      pid: null,
    };
    await writeFile(lockPath, `${JSON.stringify(legacyRecord)}\n`, { mode: 0o600 });

    assert.equal(await claimPersistentPdfImportAdmission(outputRoot), null);

    await writeFile(lockPath, `${JSON.stringify({ ...legacyRecord, createdAt: 0 })}\n`);
    assert.equal(await claimPersistentPdfImportAdmission(outputRoot), null);
    assert.equal((await stat(lockPath)).isFile(), true);
  });
});

test("two reclaimers both fail closed on the same stale legacy lock", async () => {
  await withTemporaryOutputRoot(async (outputRoot) => {
    const lockPath = path.join(outputRoot, ".papertrans-pdf-import.lock");
    for (let round = 0; round < 20; round += 1) {
      await writeFile(lockPath, `${JSON.stringify({
        owner: `legacy-owner-${round}`,
        createdAt: 0,
        pid: null,
      })}\n`, { mode: 0o600 });

      const results = await Promise.all([
        claimPersistentPdfImportAdmission(outputRoot),
        claimPersistentPdfImportAdmission(outputRoot),
      ]);
      const admissions = results.filter((result) => result !== null);
      assert.equal(admissions.length, 0);
      assert.equal((await stat(lockPath)).isFile(), true);
      await rm(lockPath);
    }
  });
});

test("a stale owner-specific setup remnant is reclaimed without broad deletion", async () => {
  await withTemporaryOutputRoot(async (outputRoot) => {
    const lockPath = path.join(outputRoot, ".papertrans-pdf-import.lock");
    const owner = "12345678-1234-4234-8234-123456789abc";
    await mkdir(lockPath, { mode: 0o700 });
    await writeFile(path.join(lockPath, `${owner}.tmp`), "partial", { mode: 0o600 });
    await utimes(lockPath, new Date(0), new Date(0));

    const admission = await claimPersistentPdfImportAdmission(outputRoot);
    assert.notEqual(admission, null);
    assert.equal((await stat(admission.lockPath)).isDirectory(), true);
    await releasePersistentPdfImportAdmission(admission);
  });
});

test("a detached worker process group keeps the persistent admission active", {
  skip: process.platform === "win32",
}, async () => {
  await withTemporaryOutputRoot(async (outputRoot) => {
    const worker = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
      detached: true,
      stdio: "ignore",
    });
    await once(worker, "spawn");
    assert.notEqual(worker.pid, undefined);

    const admission = await claimPersistentPdfImportAdmission(outputRoot);
    assert.notEqual(admission, null);
    try {
      await writePdfImportLock(admission, worker.pid);
      assert.equal(await claimPersistentPdfImportAdmission(outputRoot), null);

      const exitPromise = once(worker, "exit");
      process.kill(-worker.pid, "SIGTERM");
      await exitPromise;

      const replacement = await claimPersistentPdfImportAdmission(outputRoot);
      assert.notEqual(replacement, null);
      await releasePersistentPdfImportAdmission(replacement);
    } finally {
      try {
        process.kill(-worker.pid, "SIGKILL");
      } catch {
        // The expected path already reaped the detached worker.
      }
      await releasePersistentPdfImportAdmission(admission);
    }
  });
});
