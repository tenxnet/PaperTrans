"use client";

import {
  ArrowLeft,
  ArrowSquareOut,
  BookOpenText,
  CheckCircle,
  ChatCircleDots,
  ClockCounterClockwise,
  DownloadSimple,
  FileText,
  FunnelSimple,
  MagnifyingGlass,
  Plus,
  SpinnerGap,
  Tag,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { PaperStatus, PaperSummary } from "@/lib/paper-library";

type Filter = "all" | "active" | "review";
type Sort = "updated" | "title" | "arxiv";

const STATUS_LABEL: Record<PaperStatus, string> = {
  prepared: "準備済み",
  translating: "翻訳中",
  ready_to_finalize: "出力待ち",
  completed: "完了",
  needs_review: "要確認",
  failed: "失敗",
};

function isActive(paper: PaperSummary) {
  return ["prepared", "translating", "ready_to_finalize"].includes(paper.status);
}

function needsReview(paper: PaperSummary) {
  return paper.status === "needs_review" || paper.status === "failed" || paper.qa.status === "failed";
}

function formatDate(value: string) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("ja-JP", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function progressPercent(paper: PaperSummary) {
  if (!paper.progress.total) return paper.status === "completed" ? 100 : 0;
  return Math.round((paper.progress.completed / paper.progress.total) * 100);
}

function StatusIcon({ paper }: { paper: PaperSummary }) {
  if (paper.status === "completed" && paper.qa.status === "passed") {
    return <CheckCircle weight="fill" aria-hidden="true" />;
  }
  if (needsReview(paper)) return <WarningCircle weight="fill" aria-hidden="true" />;
  return <SpinnerGap className="spin" aria-hidden="true" />;
}

export function PaperLibrary({ initialPapers }: { initialPapers: PaperSummary[] }) {
  const [papers, setPapers] = useState(initialPapers);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("updated");
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");
  const [tagSaving, setTagSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [showRequest, setShowRequest] = useState(false);
  const [arxivDraft, setArxivDraft] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const selected = papers.find((paper) => paper.slug === selectedSlug) ?? null;
  const counts = useMemo(() => ({
    all: papers.length,
    active: papers.filter(isActive).length,
    review: papers.filter(needsReview).length,
  }), [papers]);

  const tags = useMemo(() => {
    const values = new Map<string, number>();
    for (const paper of papers) {
      for (const item of paper.tags) values.set(item, (values.get(item) ?? 0) + 1);
    }
    return [...values].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ja"));
  }, [papers]);

  const visiblePapers = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ja");
    return papers
      .filter((paper) => {
        if (filter === "active" && !isActive(paper)) return false;
        if (filter === "review" && !needsReview(paper)) return false;
        if (tagFilter && !paper.tags.includes(tagFilter)) return false;
        if (!needle) return true;
        return [paper.title, paper.requestedArxivId, paper.resolvedArxivId, ...paper.tags]
          .some((value) => value.toLocaleLowerCase("ja").includes(needle));
      })
      .sort((a, b) => {
        if (sort === "title") return a.title.localeCompare(b.title, "en");
        if (sort === "arxiv") return b.resolvedArxivId.localeCompare(a.resolvedArxivId, "en");
        return Date.parse(b.updatedAt) - Date.parse(a.updatedAt);
      });
  }, [papers, query, filter, tagFilter, sort]);

  async function refresh(silent = false) {
    try {
      const response = await fetch("/api/library", { cache: "no-store" });
      if (!response.ok) throw new Error("refresh failed");
      const body = (await response.json()) as { papers: PaperSummary[] };
      setPapers(body.papers);
      if (!silent) setNotice("ライブラリを更新しました");
    } catch {
      if (!silent) setNotice("更新できませんでした");
    }
  }

  useEffect(() => {
    if (!papers.some(isActive)) return;
    const timer = window.setInterval(() => void refresh(true), 8000);
    return () => window.clearInterval(timer);
  }, [papers]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") {
        if (showRequest) setShowRequest(false);
        else if (selectedSlug) setSelectedSlug(null);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedSlug, showRequest]);

  async function persistTags(slug: string, nextTags: string[]) {
    setTagSaving(true);
    setNotice("");
    try {
      const response = await fetch(`/api/papers/${encodeURIComponent(slug)}/tags`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tags: nextTags }),
      });
      const body = (await response.json()) as { tags?: string[]; error?: string };
      if (!response.ok || !body.tags) throw new Error(body.error ?? "タグを保存できませんでした");
      setPapers((current) => current.map((paper) => (
        paper.slug === slug ? { ...paper, tags: body.tags ?? [] } : paper
      )));
      setNotice("タグを保存しました");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "タグを保存できませんでした");
    } finally {
      setTagSaving(false);
    }
  }

  function addTag(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    const next = tagDraft.trim().replace(/\s+/g, " ");
    if (!next || selected.tags.some((tag) => tag.toLocaleLowerCase("ja") === next.toLocaleLowerCase("ja"))) {
      setTagDraft("");
      return;
    }
    setTagDraft("");
    void persistTags(selected.slug, [...selected.tags, next]);
  }

  async function copyRequest() {
    const id = arxivDraft.trim() || "2510.09023v1";
    try {
      await navigator.clipboard.writeText(`arXiv:${id} をPaperTransで全文日本語訳してください。`);
      setNotice("ChatGPTへの依頼文をコピーしました");
      setShowRequest(false);
    } catch {
      setNotice("コピーできませんでした。依頼文を選択してコピーしてください");
    }
  }

  function setNavigation(next: Filter) {
    setFilter(next);
    setTagFilter(null);
    setSelectedSlug(null);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><BookOpenText weight="duotone" aria-hidden="true" /></span>
          <span>PaperTrans</span>
        </div>

        <button className="request-card" type="button" onClick={() => setShowRequest(true)}>
          <ChatCircleDots weight="duotone" aria-hidden="true" />
          <span><strong>ChatGPTで翻訳</strong><small>arXiv HTMLから作成</small></span>
        </button>

        <nav aria-label="ライブラリ">
          <p className="nav-heading">ライブラリ</p>
          <button className={filter === "all" && !tagFilter ? "nav-item active" : "nav-item"} onClick={() => setNavigation("all")}>
            <FileText aria-hidden="true" /><span>すべての論文</span><b>{counts.all}</b>
          </button>
          <button className={filter === "active" ? "nav-item active" : "nav-item"} onClick={() => setNavigation("active")}>
            <SpinnerGap aria-hidden="true" /><span>翻訳中</span><b>{counts.active}</b>
          </button>
        </nav>

        {counts.review > 0 && (
          <section className="sidebar-notices" aria-label="お知らせ">
            <p className="nav-heading">お知らせ</p>
            <button
              className={filter === "review" ? "review-notice active" : "review-notice"}
              type="button"
              onClick={() => setNavigation("review")}
            >
              <WarningCircle weight="fill" aria-hidden="true" />
              <span><strong>確認が必要です</strong><small>{counts.review}件の論文に問題があります</small></span>
              <b>{counts.review}</b>
            </button>
          </section>
        )}

        <div className="tag-nav">
          <p className="nav-heading"><span>タグ</span><Tag aria-hidden="true" /></p>
          {tags.length ? tags.map(([tag, count]) => (
            <button
              key={tag}
              className={tagFilter === tag ? "tag-filter active" : "tag-filter"}
              onClick={() => { setTagFilter(tagFilter === tag ? null : tag); setFilter("all"); setSelectedSlug(null); }}
            >
              <span>{tag}</span><b>{count}</b>
            </button>
          )) : <p className="tag-empty">論文を開いてタグを追加できます</p>}
        </div>

        <div className="sidebar-footer">
          <p><span className="local-dot" />ローカル保存</p>
          <small>論文と翻訳はこのMac内だけに保存されます</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="workspace-bar">
          <label className="search-field">
            <MagnifyingGlass aria-hidden="true" />
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="タイトル、arXiv ID、タグを検索…"
              aria-label="論文を検索"
            />
            <kbd>⌘ K</kbd>
          </label>
          <div className="workspace-actions">
            {notice && <span className="notice" role="status">{notice}</span>}
            <button className="icon-button" type="button" onClick={() => void refresh()} title="更新">
              <ClockCounterClockwise aria-hidden="true" />
            </button>
            <button className="primary-button" type="button" onClick={() => setShowRequest(true)}>
              <Plus aria-hidden="true" />新しい翻訳
            </button>
          </div>
        </header>

        {selected ? (
          <section className="reader-layout">
            <div className="reader-main">
              <div className="reader-header">
                <button className="back-button" type="button" onClick={() => setSelectedSlug(null)}>
                  <ArrowLeft aria-hidden="true" />ライブラリ
                </button>
                <div className="reader-title">
                  <small>arXiv:{selected.resolvedArxivId}</small>
                  <h1>{selected.title}</h1>
                </div>
                <div className="reader-actions">
                  {selected.downloadUrl && (
                    <a className="secondary-button" href={selected.downloadUrl}>
                      <DownloadSimple aria-hidden="true" />ZIP
                    </a>
                  )}
                  {selected.artifactUrl && (
                    <a className="primary-button" href={selected.artifactUrl} target="_blank" rel="noreferrer">
                      <ArrowSquareOut aria-hidden="true" />別タブで開く
                    </a>
                  )}
                </div>
              </div>
              {selected.artifactUrl ? (
                <iframe className="paper-frame" title={`${selected.title} 日本語訳`} src={`${selected.artifactUrl}?embed=1`} />
              ) : (
                <div className="reader-empty"><SpinnerGap className="spin" /><p>翻訳HTMLの完成を待っています</p></div>
              )}
            </div>

            <aside className="inspector">
              <section>
                <p className="inspector-label">状態</p>
                <div className={`status-badge ${needsReview(selected) ? "review" : selected.status}`}>
                  <StatusIcon paper={selected} />{STATUS_LABEL[selected.status]}
                </div>
                <div className="progress-track" aria-label={`翻訳進捗 ${progressPercent(selected)}%`}>
                  <span style={{ width: `${progressPercent(selected)}%` }} />
                </div>
                <p className="progress-copy">{selected.progress.completed} / {selected.progress.total} チャンク</p>
              </section>

              <section>
                <p className="inspector-label">品質検査</p>
                <dl className="qa-grid">
                  <div><dt>図</dt><dd>{selected.qa.figures}</dd></div>
                  <div><dt>表</dt><dd>{selected.qa.tables}</dd></div>
                  <div><dt>数式</dt><dd>{selected.qa.math}</dd></div>
                  <div><dt>参考文献</dt><dd>{selected.qa.bibliographyEntries}</dd></div>
                </dl>
                <p className={`qa-result ${selected.qa.status}`}>
                  {selected.qa.status === "passed" ? <CheckCircle weight="fill" /> : <WarningCircle weight="fill" />}
                  {selected.qa.status === "passed" ? "構造QAを通過" : "QA結果を確認してください"}
                </p>
                {!selected.qa.browserChecked && <p className="qa-note">ブラウザDOM検査は未実施です</p>}
              </section>

              <section>
                <p className="inspector-label">タグ</p>
                <div className="paper-tags">
                  {selected.tags.map((tag) => (
                    <button key={tag} className="tag-chip removable" disabled={tagSaving} onClick={() => void persistTags(selected.slug, selected.tags.filter((item) => item !== tag))}>
                      {tag}<X aria-hidden="true" />
                    </button>
                  ))}
                </div>
                <form className="tag-form" onSubmit={addTag}>
                  <input value={tagDraft} onChange={(event) => setTagDraft(event.target.value)} placeholder="タグを追加" maxLength={32} />
                  <button type="submit" disabled={tagSaving || !tagDraft.trim()} title="タグを追加"><Plus aria-hidden="true" /></button>
                </form>
              </section>

              <section className="paper-meta">
                <p className="inspector-label">論文情報</p>
                <dl>
                  <div><dt>Provider</dt><dd>{selected.provider}</dd></div>
                  <div><dt>更新</dt><dd>{formatDate(selected.updatedAt)}</dd></div>
                </dl>
                {selected.sourceUrl && <a href={selected.sourceUrl} target="_blank" rel="noreferrer">arXiv原文を開く<ArrowSquareOut /></a>}
              </section>
            </aside>
          </section>
        ) : (
          <section className="library-view">
            <div className="library-heading">
              <div>
                <p className="eyebrow">LOCAL PAPER LIBRARY</p>
                <h1>{tagFilter ? `# ${tagFilter}` : filter === "active" ? "翻訳中" : filter === "review" ? "要確認" : "論文ライブラリ"}</h1>
                <p>{visiblePapers.length}件の論文を表示しています</p>
              </div>
              <label className="sort-control"><FunnelSimple aria-hidden="true" /><span>並べ替え</span>
                <select value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
                  <option value="updated">更新が新しい順</option>
                  <option value="title">タイトル順</option>
                  <option value="arxiv">arXiv ID順</option>
                </select>
              </label>
            </div>

            <div className="paper-list" role="list">
              {visiblePapers.map((paper) => (
                <article key={paper.slug} className="paper-row" role="listitem">
                  <button className="paper-open" type="button" onClick={() => setSelectedSlug(paper.slug)} aria-label={`${paper.title}を開く`}>
                    <span className="paper-type"><FileText weight="duotone" aria-hidden="true" /></span>
                    <span className="paper-copy">
                      <span className="paper-kicker">
                        <span>arXiv:{paper.resolvedArxivId}</span>
                        <span className={`status-inline ${needsReview(paper) ? "review" : paper.status}`}><StatusIcon paper={paper} />{STATUS_LABEL[paper.status]}</span>
                      </span>
                      <strong>{paper.title}</strong>
                      <span className="paper-subline">
                        <span>{paper.progress.completed}/{paper.progress.total} チャンク</span>
                        <span>更新 {formatDate(paper.updatedAt)}</span>
                        {paper.qa.status === "passed" && <span>QA通過</span>}
                      </span>
                      {paper.tags.length > 0 && <span className="paper-tag-row">{paper.tags.map((tag) => <span className="tag-chip" key={tag}>{tag}</span>)}</span>}
                    </span>
                    <span className="row-progress"><span style={{ width: `${progressPercent(paper)}%` }} /></span>
                  </button>
                  <div className="row-actions">
                    {paper.downloadUrl && <a href={paper.downloadUrl} title="Offline ZIPをダウンロード"><DownloadSimple aria-hidden="true" /></a>}
                    {paper.artifactUrl && <a href={paper.artifactUrl} target="_blank" rel="noreferrer" title="別タブで開く"><ArrowSquareOut aria-hidden="true" /></a>}
                  </div>
                </article>
              ))}
              {!visiblePapers.length && (
                <div className="empty-state">
                  <MagnifyingGlass aria-hidden="true" />
                  <h2>一致する論文がありません</h2>
                  <p>検索語やフィルターを変更してみてください。</p>
                  <button className="secondary-button" onClick={() => { setQuery(""); setFilter("all"); setTagFilter(null); }}>条件をクリア</button>
                </div>
              )}
            </div>
          </section>
        )}
      </section>

      {showRequest && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowRequest(false)}>
          <section className="request-modal" role="dialog" aria-modal="true" aria-labelledby="request-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className="modal-close" type="button" onClick={() => setShowRequest(false)} title="閉じる"><X aria-hidden="true" /></button>
            <span className="modal-icon"><ChatCircleDots weight="duotone" aria-hidden="true" /></span>
            <p className="eyebrow">CHATGPT CONNECTOR</p>
            <h2 id="request-title">新しい翻訳を依頼</h2>
            <p>arXiv IDを入力すると、ChatGPTへ渡す依頼文をコピーします。接続済みのChatGPTで貼り付けて実行してください。</p>
            <label><span>arXiv ID</span><input autoFocus value={arxivDraft} onChange={(event) => setArxivDraft(event.target.value)} placeholder="例: 2510.09023v1" /></label>
            <div className="prompt-preview">arXiv:{arxivDraft.trim() || "2510.09023v1"} をPaperTransで全文日本語訳してください。</div>
            <button className="primary-button wide" type="button" onClick={() => void copyRequest()}>依頼文をコピー</button>
          </section>
        </div>
      )}
    </main>
  );
}
