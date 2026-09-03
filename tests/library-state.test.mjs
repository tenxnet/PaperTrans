import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, rm, symlink, utimes, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { promisify } from "node:util";
import { Worker } from "node:worker_threads";

import {
  LibraryMetadataError,
  savePaperLibraryStateAt,
} from "../lib/library-state.ts";

const execFileAsync = promisify(execFile);

async function processStartedAt(pid) {
  try {
    const stat = await readFile(`/proc/${pid}/stat`, "utf8");
    const fields = stat.slice(stat.lastIndexOf(")") + 2).trim().split(/\s+/);
    const startTicks = Number(fields[19]);
    if (Number.isSafeInteger(startTicks) && startTicks > 0) return startTicks;
  } catch {
    // macOS uses the ps fallback below.
  }
  const { stdout } = await execFileAsync(
    "/bin/ps",
    ["-o", "lstart=", "-p", String(pid)],
    { encoding: "utf8", env: { ...process.env, LANG: "C", LC_ALL: "C" } },
  );
  return Math.floor(Date.parse(stdout.trim()) / 1000);
}

async function withTemporaryDataRoot(run) {
  const root = await mkdtemp(path.join(os.tmpdir(), "papertrans-library-"));
  try {
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test("concurrent updates preserve independent papers", async () => {
  await withTemporaryDataRoot(async (root) => {
    await Promise.all([
      savePaperLibraryStateAt(root, "paper-one", { tags: ["security"] }),
      savePaperLibraryStateAt(root, "paper-two", { favorite: true }),
    ]);

    const metadata = JSON.parse(await readFile(path.join(root, "library.json"), "utf8"));
    assert.deepEqual(metadata.papers["paper-one"], {
      tags: ["security"],
      isRead: false,
      favorite: false,
    });
    assert.deepEqual(metadata.papers["paper-two"], {
      tags: [],
      isRead: false,
      favorite: true,
    });
  });
});

test("concurrent partial updates preserve fields on the same paper", async () => {
  await withTemporaryDataRoot(async (root) => {
    await Promise.all([
      savePaperLibraryStateAt(root, "paper", { tags: ["ml"] }),
      savePaperLibraryStateAt(root, "paper", { isRead: true }),
      savePaperLibraryStateAt(root, "paper", { favorite: true }),
    ]);

    const metadata = JSON.parse(await readFile(path.join(root, "library.json"), "utf8"));
    assert.deepEqual(metadata.papers.paper, {
      tags: ["ml"],
      isRead: true,
      favorite: true,
    });
  });
});

test("concurrent additive tag updates do not lose either tab's tag", async () => {
  await withTemporaryDataRoot(async (root) => {
    await Promise.all([
      savePaperLibraryStateAt(root, "paper", { addTags: ["ml"] }),
      savePaperLibraryStateAt(root, "paper", { addTags: ["security"] }),
    ]);

    const metadata = JSON.parse(await readFile(path.join(root, "library.json"), "utf8"));
    assert.deepEqual(new Set(metadata.papers.paper.tags), new Set(["ml", "security"]));
  });
});

test("corrupt metadata fails closed without overwriting the source", async () => {
  await withTemporaryDataRoot(async (root) => {
    const metadataPath = path.join(root, "library.json");
    const corrupt = "{not-json\n";
    await writeFile(metadataPath, corrupt, "utf8");

    await assert.rejects(
      savePaperLibraryStateAt(root, "paper", { favorite: true }),
      LibraryMetadataError,
    );
    assert.equal(await readFile(metadataPath, "utf8"), corrupt);
  });
});

test("invalid existing paper fields fail closed during an unrelated patch", async () => {
  await withTemporaryDataRoot(async (root) => {
    const metadataPath = path.join(root, "library.json");
    const invalid = `${JSON.stringify({
      version: 1,
      papers: {
        paper: { tags: [], isRead: "true", favorite: false },
      },
    }, null, 2)}\n`;
    await writeFile(metadataPath, invalid, "utf8");

    await assert.rejects(
      savePaperLibraryStateAt(root, "paper", { favorite: true }),
      LibraryMetadataError,
    );
    assert.equal(await readFile(metadataPath, "utf8"), invalid);
  });
});

test("empty, non-object, and unknown library patches are rejected", async () => {
  await withTemporaryDataRoot(async (root) => {
    for (const patch of [{}, [], 42, null, { unknown: true }, { favorite: true, extra: 1 }]) {
      await assert.rejects(
        savePaperLibraryStateAt(root, "paper", patch),
        /library update must be a JSON object|library update must contain a supported field/,
      );
    }
  });
});

test("schema-v1 tag-only records migrate missing booleans without data loss", async () => {
  await withTemporaryDataRoot(async (root) => {
    const metadataPath = path.join(root, "library.json");
    await writeFile(metadataPath, `${JSON.stringify({
      version: 1,
      papers: {
        legacy: { tags: ["ml"] },
      },
    }, null, 2)}\n`, "utf8");

    await savePaperLibraryStateAt(root, "new-paper", { favorite: true });

    const metadata = JSON.parse(await readFile(metadataPath, "utf8"));
    assert.deepEqual(metadata.papers.legacy, {
      tags: ["ml"],
      isRead: false,
      favorite: false,
    });
    assert.equal(metadata.papers["new-paper"].favorite, true);
  });
});

test("a partial owner record from a dead process can be reclaimed", async () => {
  await withTemporaryDataRoot(async (root) => {
    const lockPath = path.join(root, ".library.lock");
    const owner = "12345678-1234-4234-8234-123456789abc";
    const instance = "aaaaaaaa-1234-4234-8234-123456789abc";
    await mkdir(lockPath);
    await writeFile(path.join(lockPath, `${owner}.2147483647.0.${instance}.json`), "{", "utf8");
    await utimes(lockPath, new Date(0), new Date(0));

    const state = await savePaperLibraryStateAt(root, "paper", { favorite: true });

    assert.equal(state.favorite, true);
  });
});

test("a current PID record with a mismatched start time is reclaimed after PID reuse", async () => {
  await withTemporaryDataRoot(async (root) => {
    const lockPath = path.join(root, ".library.lock");
    const owner = "12345678-1234-4234-8234-123456789abc";
    const oldInstance = "aaaaaaaa-1234-4234-8234-123456789abc";
    const mismatchedStart = (await processStartedAt(process.pid)) + 1;
    const record = path.join(
      lockPath,
      `${owner}.${process.pid}.${mismatchedStart}.${oldInstance}.json`,
    );
    await mkdir(lockPath);
    await writeFile(record, "{", "utf8");
    await utimes(record, new Date(0), new Date(0));

    const state = await savePaperLibraryStateAt(root, "paper", { isRead: true });

    assert.equal(state.isRead, true);
  });
});

test("worker threads sharing a PID serialize without losing updates", async () => {
  await withTemporaryDataRoot(async (root) => {
    const moduleUrl = pathToFileURL(path.resolve("lib/library-state.ts")).href;
    const workerSource = `
      import { parentPort, workerData } from "node:worker_threads";
      import { savePaperLibraryStateAt } from ${JSON.stringify(moduleUrl)};
      try {
        await savePaperLibraryStateAt(
          workerData.root,
          \`worker-\${workerData.index}\`,
          { favorite: true },
        );
        parentPort.postMessage({ ok: true });
      } catch (error) {
        parentPort.postMessage({ ok: false, error: String(error) });
      }
    `;
    const workers = Array.from({ length: 20 }, (_, index) => new Worker(
      workerSource,
      { eval: true, type: "module", workerData: { root, index } },
    ));
    try {
      const results = await Promise.all(workers.map(async (worker) => {
        const [message] = await once(worker, "message");
        return message;
      }));
      assert.ok(results.every((result) => result.ok), JSON.stringify(results));
    } finally {
      await Promise.all(workers.map((worker) => worker.terminate()));
    }

    const metadata = JSON.parse(await readFile(path.join(root, "library.json"), "utf8"));
    assert.equal(Object.keys(metadata.papers).length, 20);
  });
});

test("a live foreign PID with a mismatched start time is reclaimed after PID reuse", async () => {
  await withTemporaryDataRoot(async (root) => {
    const child = spawn(
      process.execPath,
      ["-e", "process.stdout.write('ready\\n'); setInterval(() => {}, 1000)"],
      { stdio: ["ignore", "pipe", "ignore"] },
    );
    try {
      await once(child.stdout, "data");
      const lockPath = path.join(root, ".library.lock");
      const owner = "12345678-1234-4234-8234-123456789abc";
      const instance = "aaaaaaaa-1234-4234-8234-123456789abc";
      const mismatchedStart = (await processStartedAt(child.pid)) + 1;
      await mkdir(lockPath);
      await writeFile(
        path.join(lockPath, `${owner}.${child.pid}.${mismatchedStart}.${instance}.json`),
        "{",
        "utf8",
      );

      const state = await savePaperLibraryStateAt(root, "paper", { favorite: true });

      assert.equal(state.favorite, true);
    } finally {
      child.kill();
      await once(child, "exit").catch(() => undefined);
    }
  });
});

test("a live foreign process lock is preserved until its owner releases it", async () => {
  await withTemporaryDataRoot(async (root) => {
    const child = spawn(
      process.execPath,
      ["-e", "process.stdout.write('ready\\n'); setInterval(() => {}, 1000)"],
      { stdio: ["ignore", "pipe", "ignore"] },
    );
    try {
      await once(child.stdout, "data");
      const lockPath = path.join(root, ".library.lock");
      const owner = "12345678-1234-4234-8234-123456789abc";
      const instance = "aaaaaaaa-1234-4234-8234-123456789abc";
      const record = path.join(
        lockPath,
        `${owner}.${child.pid}.${await processStartedAt(child.pid)}.${instance}.json`,
      );
      await mkdir(lockPath);
      await writeFile(record, "{", "utf8");

      let ownerReleased = false;
      const release = new Promise((resolve, reject) => {
        setTimeout(async () => {
          try {
            await rm(lockPath, { recursive: true });
            ownerReleased = true;
            resolve();
          } catch (error) {
            reject(error);
          }
        }, 200);
      });
      const started = Date.now();
      const state = await savePaperLibraryStateAt(root, "paper", { favorite: true });
      const saveElapsed = Date.now() - started;
      const saveWaitedForOwner = ownerReleased;
      await release;

      assert.equal(state.favorite, true);
      assert.equal(saveWaitedForOwner, true, "writer must not steal an active process lock");
      assert.ok(saveElapsed >= 150, "writer must wait for the active process lock");
    } finally {
      child.kill();
      await once(child, "exit").catch(() => undefined);
    }
  });
});

test("a lock symlink fails closed without deleting its target", async () => {
  await withTemporaryDataRoot(async (root) => {
    const outside = await mkdtemp(path.join(os.tmpdir(), "papertrans-library-outside-"));
    const owner = "12345678-1234-4234-8234-123456789abc";
    const instance = "aaaaaaaa-1234-4234-8234-123456789abc";
    const record = path.join(outside, `${owner}.2147483647.0.${instance}.json`);
    try {
      await writeFile(record, "{}\n", "utf8");
      await symlink(outside, path.join(root, ".library.lock"));

      await assert.rejects(
        savePaperLibraryStateAt(root, "paper", { favorite: true }),
        LibraryMetadataError,
      );
      assert.equal(await readFile(record, "utf8"), "{}\n");
    } finally {
      await rm(outside, { recursive: true, force: true });
    }
  });
});
