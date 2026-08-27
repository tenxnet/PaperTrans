import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir, open, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

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

export async function POST(request: Request) {
  const form = await request.formData();
  const paper = form.get("paper");
  if (!(paper instanceof File) || (!paper.name.toLowerCase().endsWith(".pdf") && paper.type !== "application/pdf")) {
    return NextResponse.json({ error: "PDFを選択してください" }, { status: 400 });
  }
  const bytes = Buffer.from(await paper.arrayBuffer());
  if (bytes.length < 5 || bytes.subarray(0, 5).toString() !== "%PDF-") {
    return NextResponse.json({ error: "PDFヘッダーを確認できません" }, { status: 400 });
  }

  const dataRoot = path.join(process.cwd(), "data", "papers");
  const digest = createHash("sha256").update(bytes).digest("hex");
  const duplicate = await existingDigest(dataRoot, digest);
  if (duplicate) return NextResponse.json({ error: `同一PDFは取込済みです: ${duplicate}` }, { status: 409 });

  let slug = slugify(paper.name);
  try {
    await readFile(path.join(dataRoot, slug, "source.pdf"));
    slug = `${slug}-${digest.slice(0, 8)}`;
  } catch { /* available */ }
  const paperDir = path.join(dataRoot, slug);
  const outputDir = path.join(process.cwd(), "output", slug);
  await mkdir(paperDir, { recursive: true });
  await mkdir(outputDir, { recursive: true });
  await writeFile(path.join(paperDir, "source.pdf"), bytes, { flag: "wx" });

  const log = await open(path.join(outputDir, "job.log"), "a");
  const child = spawn(
    path.join(process.cwd(), ".venv", "bin", "papertrans"),
    ["pipeline", path.join(paperDir, "source.pdf"), "--slug", slug, "--repo-root", process.cwd()],
    { cwd: process.cwd(), detached: true, stdio: ["ignore", log.fd, log.fd] },
  );
  child.unref();
  await log.close();
  return NextResponse.json({ slug, status: "started" }, { status: 202 });
}
