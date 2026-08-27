import { readFile } from "node:fs/promises";
import path from "node:path";
import { UploadForm } from "./upload-form";

export const dynamic = "force-dynamic";

type Item = { kind: string; japanese?: string; warnings?: string[] };
type DocumentSummary = {
  title: string;
  page_count: number;
  status: string;
  pages: Array<{ items: Item[] }>;
};

async function loadSample(): Promise<DocumentSummary | null> {
  try {
    const raw = await readFile(path.join(process.cwd(), "output/llmmap/work/document.json"), "utf8");
    return JSON.parse(raw) as DocumentSummary;
  } catch {
    return null;
  }
}

export default async function Home() {
  const document = await loadSample();
  const items = document?.pages.flatMap((page) => page.items) ?? [];
  const translated = items.filter(
    (item) => ["heading", "paragraph"].includes(item.kind) && item.japanese?.trim(),
  ).length;
  const warningBlocks = items.filter((item) => item.warnings?.length).length;

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="product">PaperTrans</p>
          <h1>学術PDF → 日本語HTML</h1>
        </div>
        <UploadForm />
      </header>

      <section className="summary" aria-label="処理状況">
        <div><span>テスト論文</span><strong>{document?.title ?? "未生成"}</strong></div>
        <div><span>ページ</span><strong>{document?.page_count ?? 0}</strong></div>
        <div><span>翻訳ブロック</span><strong>{translated}</strong></div>
        <div><span>要確認</span><strong>{warningBlocks}</strong></div>
        <div><span>状態</span><strong>{document?.status ?? "not_started"}</strong></div>
      </section>

      {document ? (
        <section className="reader-frame">
          <div className="reader-toolbar">
            <span>原文の図表・数式を埋め込んだ生成HTML</span>
            <a href="/api/artifacts/llmmap/index.html" target="_blank" rel="noreferrer">別タブで開く</a>
          </div>
          <iframe title="LLMmap 日本語訳" src="/api/artifacts/llmmap/index.html" />
        </section>
      ) : (
        <p className="empty">CLIでサンプルパイプラインを実行すると、ここにHTMLが表示されます。</p>
      )}
    </main>
  );
}
