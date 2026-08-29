"use client";

import {
  ArrowLeft,
  ArrowSquareOut,
  BookOpenText,
  CheckCircle,
  ChatCircleDots,
  ClockCounterClockwise,
  FileText,
  FunnelSimple,
  ListBullets,
  MagnifyingGlass,
  Plus,
  SpinnerGap,
  Star,
  Tag,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { PaperStatus, PaperSummary } from "@/lib/paper-library";
import {
  APP_LOCALE_STORAGE_KEY,
  DEFAULT_APP_LOCALE,
  isAppLocale,
  UI_TEXT,
  type AppLocale,
} from "@/lib/i18n";

type Filter = "all" | "active" | "review" | "unread" | "favorites";
type Sort = "updated" | "added" | "published" | "author" | "title" | "tag";
type LibraryPatch = { tags?: string[]; isRead?: boolean; favorite?: boolean };
type TocEntry = { id: string; label: string; level: 2 | 3 };

function isActive(paper: PaperSummary) {
  return ["prepared", "translating", "ready_to_finalize"].includes(paper.status);
}

function needsReview(paper: PaperSummary) {
  return paper.status === "needs_review" || paper.status === "failed" || paper.qa.status === "failed";
}

function formatDate(value: string, locale: AppLocale) {
  const date = new Date(value);
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatPaperDate(value: string | null, locale: AppLocale) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

function authorLabel(paper: PaperSummary, locale: AppLocale) {
  const text = UI_TEXT[locale];
  if (!paper.authors.length) return text.noAuthor;
  return paper.authors.length > 1 ? text.otherAuthors(paper.authors[0]) : paper.authors[0];
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
  const [locale, setLocale] = useState<AppLocale>(DEFAULT_APP_LOCALE);
  const [papers, setPapers] = useState(initialPapers);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("updated");
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");
  const [librarySaving, setLibrarySaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [showRequest, setShowRequest] = useState(false);
  const [arxivDraft, setArxivDraft] = useState("");
  const [tocEntries, setTocEntries] = useState<TocEntry[]>([]);
  const [activeTocId, setActiveTocId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const paperFrameRef = useRef<HTMLIFrameElement>(null);
  const tocListRef = useRef<HTMLElement>(null);
  const tocCleanupRef = useRef<(() => void) | null>(null);
  const text = UI_TEXT[locale];

  const selected = papers.find((paper) => paper.slug === selectedSlug) ?? null;
  const counts = useMemo(() => ({
    all: papers.length,
    active: papers.filter(isActive).length,
    review: papers.filter(needsReview).length,
    unread: papers.filter((paper) => !paper.isRead).length,
    favorites: papers.filter((paper) => paper.favorite).length,
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
        if (filter === "unread" && paper.isRead) return false;
        if (filter === "favorites" && !paper.favorite) return false;
        if (tagFilter && !paper.tags.includes(tagFilter)) return false;
        if (!needle) return true;
        return [paper.title, ...paper.authors, paper.requestedArxivId, paper.resolvedArxivId, ...paper.tags]
          .some((value) => value.toLocaleLowerCase("ja").includes(needle));
      })
      .sort((a, b) => {
        if (sort === "title") return a.title.localeCompare(b.title, "en");
        if (sort === "author") return authorLabel(a, locale).localeCompare(authorLabel(b, locale), locale);
        if (sort === "tag") return (a.tags[0] ?? "\uffff").localeCompare(b.tags[0] ?? "\uffff", "ja");
        if (sort === "added") return Date.parse(b.createdAt) - Date.parse(a.createdAt);
        if (sort === "published") return Date.parse(b.publishedAt ?? "1970-01-01") - Date.parse(a.publishedAt ?? "1970-01-01");
        return Date.parse(b.updatedAt) - Date.parse(a.updatedAt);
      });
  }, [papers, query, filter, tagFilter, sort, locale]);

  useEffect(() => {
    const savedLocale = window.localStorage.getItem(APP_LOCALE_STORAGE_KEY);
    if (isAppLocale(savedLocale)) setLocale(savedLocale);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  useEffect(() => {
    tocCleanupRef.current?.();
    tocCleanupRef.current = null;
    setTocEntries([]);
    setActiveTocId(null);
    return () => {
      tocCleanupRef.current?.();
      tocCleanupRef.current = null;
    };
  }, [selectedSlug]);

  useEffect(() => {
    if (!activeTocId || !tocListRef.current) return;
    const activeButton = Array.from(tocListRef.current.querySelectorAll<HTMLButtonElement>("[data-toc-id]"))
      .find((button) => button.dataset.tocId === activeTocId);
    activeButton?.scrollIntoView({ block: "nearest" });
  }, [activeTocId]);

  function changeLocale(nextLocale: AppLocale) {
    setLocale(nextLocale);
    window.localStorage.setItem(APP_LOCALE_STORAGE_KEY, nextLocale);
  }

  async function refresh(silent = false) {
    try {
      const response = await fetch("/api/library", { cache: "no-store" });
      if (!response.ok) throw new Error("refresh failed");
      const body = (await response.json()) as { papers: PaperSummary[] };
      setPapers(body.papers);
      if (!silent) setNotice(text.refreshSuccess);
    } catch {
      if (!silent) setNotice(text.refreshFailed);
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

  async function persistLibraryState(
    slug: string,
    patch: LibraryPatch,
    successMessage: string,
    silent = false,
  ) {
    setLibrarySaving(true);
    if (!silent) setNotice("");
    try {
      const response = await fetch(`/api/papers/${encodeURIComponent(slug)}/library`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch),
      });
      const body = (await response.json()) as {
        tags?: string[];
        isRead?: boolean;
        favorite?: boolean;
        error?: string;
      };
      if (!response.ok || !body.tags || typeof body.isRead !== "boolean" || typeof body.favorite !== "boolean") {
        throw new Error(body.error ?? text.librarySaveFailed);
      }
      setPapers((current) => current.map((paper) => (
        paper.slug === slug
          ? { ...paper, tags: body.tags ?? [], isRead: body.isRead ?? false, favorite: body.favorite ?? false }
          : paper
      )));
      if (!silent) setNotice(successMessage);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : text.librarySaveFailed);
    } finally {
      setLibrarySaving(false);
    }
  }

  function persistTags(slug: string, nextTags: string[]) {
    return persistLibraryState(slug, { tags: nextTags }, text.tagSaved);
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
      await navigator.clipboard.writeText(text.translationPrompt(id));
      setNotice(text.requestCopied);
      setShowRequest(false);
    } catch {
      setNotice(text.requestCopyFailed);
    }
  }

  function setNavigation(next: Filter) {
    setFilter(next);
    setTagFilter(null);
    setSelectedSlug(null);
  }

  function openPaper(paper: PaperSummary) {
    setSelectedSlug(paper.slug);
    window.scrollTo({ top: 0, left: 0 });
    if (!paper.isRead) {
      void persistLibraryState(paper.slug, { isRead: true }, "", true);
    }
  }

  function resetEmbeddedPaper(iframe: HTMLIFrameElement) {
    try {
      iframe.contentWindow?.scrollTo({ top: 0, left: 0 });
    } catch {
      // The local artifact is same-origin; fail safely if deployment changes that assumption.
    }
  }

  function initializeEmbeddedPaper(iframe: HTMLIFrameElement) {
    resetEmbeddedPaper(iframe);
    tocCleanupRef.current?.();
    tocCleanupRef.current = null;

    try {
      const frameWindow = iframe.contentWindow;
      const frameDocument = iframe.contentDocument;
      if (!frameWindow || !frameDocument) return;

      const headings = Array.from(frameDocument.querySelectorAll<HTMLElement>("article h2, article h3"));
      const entries = headings.flatMap<TocEntry>((heading, index) => {
        const labelNode = heading.querySelector<HTMLElement>(".ptx-heading-ja");
        const label = (labelNode?.textContent ?? heading.textContent ?? "").replace(/\s+/g, " ").trim();
        if (!label) return [];
        const level = heading.tagName === "H3" ? 3 : 2;
        if (!heading.id) {
          const sourceId = heading.dataset.papertransId ?? String(index + 1);
          heading.id = `papertrans-toc-${sourceId.replace(/[^A-Za-z0-9_-]/g, "-")}`;
        }
        return [{ id: heading.id, label, level }];
      });

      setTocEntries(entries);
      setActiveTocId(entries[0]?.id ?? null);
      if (!entries.length) return;

      let animationFrame = 0;
      const updateActiveSection = () => {
        frameWindow.cancelAnimationFrame(animationFrame);
        animationFrame = frameWindow.requestAnimationFrame(() => {
          let currentId = entries[0].id;
          for (const entry of entries) {
            const heading = frameDocument.getElementById(entry.id);
            if (!heading || heading.getBoundingClientRect().top > 140) break;
            currentId = entry.id;
          }
          setActiveTocId((current) => current === currentId ? current : currentId);
        });
      };

      frameWindow.addEventListener("scroll", updateActiveSection, { passive: true });
      frameWindow.addEventListener("resize", updateActiveSection);
      updateActiveSection();
      tocCleanupRef.current = () => {
        frameWindow.cancelAnimationFrame(animationFrame);
        frameWindow.removeEventListener("scroll", updateActiveSection);
        frameWindow.removeEventListener("resize", updateActiveSection);
      };
    } catch {
      setTocEntries([]);
      setActiveTocId(null);
    }
  }

  function navigateToSection(id: string) {
    try {
      const heading = paperFrameRef.current?.contentDocument?.getElementById(id);
      if (!heading) return;
      setActiveTocId(id);
      heading.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start",
      });
    } catch {
      // The local artifact is same-origin; fail safely if deployment changes that assumption.
    }
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
          <span><strong>{text.translateWithChatGPT}</strong><small>{text.createFromArxiv}</small></span>
        </button>

        <nav aria-label={text.library}>
          <p className="nav-heading">{text.library}</p>
          <button className={filter === "all" && !tagFilter ? "nav-item active" : "nav-item"} onClick={() => setNavigation("all")}>
            <FileText aria-hidden="true" /><span>{text.allPapers}</span><b>{counts.all}</b>
          </button>
          <button className={filter === "unread" ? "nav-item active" : "nav-item"} onClick={() => setNavigation("unread")}>
            <BookOpenText aria-hidden="true" /><span>{text.unread}</span><b>{counts.unread}</b>
          </button>
          <button className={filter === "favorites" ? "nav-item active" : "nav-item"} onClick={() => setNavigation("favorites")}>
            <Star weight="fill" aria-hidden="true" /><span>{text.favorites}</span><b>{counts.favorites}</b>
          </button>
          <button className={filter === "active" ? "nav-item active" : "nav-item"} onClick={() => setNavigation("active")}>
            <SpinnerGap aria-hidden="true" /><span>{text.translating}</span><b>{counts.active}</b>
          </button>
        </nav>

        {counts.review > 0 && (
          <section className="sidebar-notices" aria-label={text.notices}>
            <p className="nav-heading">{text.notices}</p>
            <button
              className={filter === "review" ? "review-notice active" : "review-notice"}
              type="button"
              onClick={() => setNavigation("review")}
            >
              <WarningCircle weight="fill" aria-hidden="true" />
              <span><strong>{text.reviewRequired}</strong><small>{text.reviewCount(counts.review)}</small></span>
              <b>{counts.review}</b>
            </button>
          </section>
        )}

        <div className="tag-nav">
          <p className="nav-heading"><span>{text.tags}</span><Tag aria-hidden="true" /></p>
          {tags.length ? tags.map(([tag, count]) => (
            <button
              key={tag}
              className={tagFilter === tag ? "tag-filter active" : "tag-filter"}
              onClick={() => { setTagFilter(tagFilter === tag ? null : tag); setFilter("all"); setSelectedSlug(null); }}
            >
              <span>{tag}</span><b>{count}</b>
            </button>
          )) : <p className="tag-empty">{text.tagEmpty}</p>}
        </div>

        <div className="sidebar-footer">
          <p><span className="local-dot" />{text.localStorage}</p>
          <small>{text.localStorageNote}</small>
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
              placeholder={text.searchPlaceholder}
              aria-label={text.searchLabel}
            />
            <kbd>⌘ K</kbd>
          </label>
          <div className="workspace-actions">
            {notice && <span className="notice" role="status">{notice}</span>}
            <label className="locale-control">
              <span>{text.language}</span>
              <select value={locale} onChange={(event) => changeLocale(event.target.value as AppLocale)} aria-label={text.language}>
                <option value="ja">日本語</option>
                <option value="en">English</option>
              </select>
            </label>
            <button className="icon-button" type="button" onClick={() => void refresh()} title={text.refresh}>
              <ClockCounterClockwise aria-hidden="true" />
            </button>
            <button className="primary-button" type="button" onClick={() => setShowRequest(true)}>
              <Plus aria-hidden="true" />{text.newTranslation}
            </button>
          </div>
        </header>

        {selected ? (
          <section className="reader-layout">
            <div className="reader-main">
              <div className="reader-header">
                <button className="back-button" type="button" onClick={() => setSelectedSlug(null)}>
                  <ArrowLeft aria-hidden="true" />{text.backToLibrary}
                </button>
                <div className="reader-title">
                  <small>arXiv:{selected.resolvedArxivId}</small>
                  <h1>{selected.title}</h1>
                </div>
                <div className="reader-actions">
                  <button
                    className={selected.favorite ? "secondary-button active" : "secondary-button"}
                    type="button"
                    disabled={librarySaving}
                    aria-pressed={selected.favorite}
                    onClick={() => void persistLibraryState(selected.slug, { favorite: !selected.favorite }, selected.favorite ? text.removedFavorite : text.addedFavorite)}
                  >
                    <Star weight={selected.favorite ? "fill" : "regular"} aria-hidden="true" />
                    {selected.favorite ? text.favorites : text.addFavorite}
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={librarySaving}
                    onClick={() => void persistLibraryState(selected.slug, { isRead: !selected.isRead }, selected.isRead ? text.markedUnread : text.markedRead)}
                  >
                    <BookOpenText aria-hidden="true" />{selected.isRead ? text.markUnread : text.markRead}
                  </button>
                </div>
              </div>
              {selected.artifactUrl ? (
                <iframe
                  ref={paperFrameRef}
                  key={selected.slug}
                  className="paper-frame"
                  title={text.translatedPaperFrame(selected.title)}
                  src={`${selected.artifactUrl}?embed=1`}
                  onLoad={(event) => initializeEmbeddedPaper(event.currentTarget)}
                />
              ) : (
                <div className="reader-empty"><SpinnerGap className="spin" /><p>{text.waitingForHtml}</p></div>
              )}
            </div>

            <aside className="inspector">
              <section className="inspector-toc">
                <p className="inspector-label"><ListBullets aria-hidden="true" />{text.tableOfContents}</p>
                {tocEntries.length ? (
                  <nav ref={tocListRef} className="toc-list" aria-label={text.tableOfContents}>
                    {tocEntries.map((entry) => (
                      <button
                        key={entry.id}
                        className={`toc-item level-${entry.level}${activeTocId === entry.id ? " active" : ""}`}
                        type="button"
                        data-toc-id={entry.id}
                        aria-current={activeTocId === entry.id ? "location" : undefined}
                        onClick={() => navigateToSection(entry.id)}
                      >
                        {entry.label}
                      </button>
                    ))}
                  </nav>
                ) : <p className="toc-empty">{text.noTableOfContents}</p>}
              </section>

              <section>
                <p className="inspector-label">{text.tags}</p>
                <div className="paper-tags">
                  {selected.tags.map((tag) => (
                    <button key={tag} className="tag-chip removable" disabled={librarySaving} onClick={() => void persistTags(selected.slug, selected.tags.filter((item) => item !== tag))}>
                      {tag}<X aria-hidden="true" />
                    </button>
                  ))}
                </div>
                <form className="tag-form" onSubmit={addTag}>
                  <input value={tagDraft} onChange={(event) => setTagDraft(event.target.value)} placeholder={text.addTag} maxLength={32} />
                  <button type="submit" disabled={librarySaving || !tagDraft.trim()} title={text.addTag}><Plus aria-hidden="true" /></button>
                </form>
              </section>

              <section className="paper-meta">
                <p className="inspector-label">{text.paperInformation}</p>
                <dl>
                  <div><dt>{text.provider}</dt><dd>{selected.provider}</dd></div>
                  <div><dt>{text.authors}</dt><dd>{selected.authors.join(" / ") || "—"}</dd></div>
                  <div><dt>{text.publishedAt}</dt><dd>{formatPaperDate(selected.publishedAt, locale)}</dd></div>
                  <div><dt>{text.addedAt}</dt><dd>{formatPaperDate(selected.createdAt, locale)}</dd></div>
                  <div><dt>{text.updatedAt}</dt><dd>{formatDate(selected.updatedAt, locale)}</dd></div>
                </dl>
                {selected.sourceUrl && <a href={selected.sourceUrl} target="_blank" rel="noreferrer">{text.openArxivSource}<ArrowSquareOut /></a>}
              </section>
            </aside>
          </section>
        ) : (
          <section className="library-view">
            <div className="library-heading">
              <div>
                <p className="eyebrow">{text.localPaperLibrary}</p>
                <h1>{tagFilter ? `# ${tagFilter}` : filter === "active" ? text.headingActive : filter === "review" ? text.headingReview : filter === "unread" ? text.headingUnread : filter === "favorites" ? text.headingFavorites : text.headingLibrary}</h1>
                <p>{text.visibleCount(visiblePapers.length)}</p>
              </div>
              <label className="sort-control"><FunnelSimple aria-hidden="true" /><span>{text.sort}</span>
                <select value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
                  <option value="updated">{text.sortUpdated}</option>
                  <option value="added">{text.sortAdded}</option>
                  <option value="published">{text.sortPublished}</option>
                  <option value="author">{text.sortAuthor}</option>
                  <option value="title">{text.sortTitle}</option>
                  <option value="tag">{text.sortTag}</option>
                </select>
              </label>
            </div>

            <div className="paper-list" role="list">
              {visiblePapers.map((paper) => (
                <article key={paper.slug} className="paper-row" role="listitem">
                  <button className="paper-open" type="button" onClick={() => openPaper(paper)} aria-label={text.openPaper(paper.title)}>
                    <span className="paper-type"><FileText weight="duotone" aria-hidden="true" /></span>
                    <span className="paper-copy">
                      <span className="paper-kicker">
                        <span>arXiv:{paper.resolvedArxivId}</span>
                        <span className={`status-inline ${needsReview(paper) ? "review" : paper.status}`}><StatusIcon paper={paper} />{text.status[paper.status]}</span>
                      </span>
                      <strong>{paper.title}</strong>
                      <span className="paper-authors">{authorLabel(paper, locale)}</span>
                      <span className="paper-subline">
                        <span>{paper.progress.completed}/{paper.progress.total} {text.chunks}</span>
                        <span>{text.published} {formatPaperDate(paper.publishedAt, locale)}</span>
                        <span>{text.added} {formatPaperDate(paper.createdAt, locale)}</span>
                        {paper.qa.status === "passed" && <span>{text.qaPassed}</span>}
                      </span>
                      {paper.tags.length > 0 && <span className="paper-tag-row">{paper.tags.map((tag) => <span className="tag-chip" key={tag}>{tag}</span>)}</span>}
                    </span>
                    <span className="row-progress"><span style={{ width: `${progressPercent(paper)}%` }} /></span>
                  </button>
                  <div className="row-actions">
                    <button
                      type="button"
                      aria-label={text.editTags}
                      onClick={() => setSelectedSlug(paper.slug)}
                    ><Tag aria-hidden="true" /></button>
                    <button
                      className={paper.favorite ? "active favorite" : ""}
                      type="button"
                      disabled={librarySaving}
                      aria-label={paper.favorite ? text.removeFavorite : text.addFavorite}
                      aria-pressed={paper.favorite}
                      onClick={() => void persistLibraryState(paper.slug, { favorite: !paper.favorite }, paper.favorite ? text.removedFavorite : text.addedFavorite)}
                    ><Star weight={paper.favorite ? "fill" : "regular"} aria-hidden="true" /></button>
                    <button
                      className={paper.isRead ? "active" : ""}
                      type="button"
                      disabled={librarySaving}
                      aria-label={paper.isRead ? text.markUnread : text.markRead}
                      aria-pressed={paper.isRead}
                      onClick={() => void persistLibraryState(paper.slug, { isRead: !paper.isRead }, paper.isRead ? text.markedUnread : text.markedRead)}
                    ><BookOpenText aria-hidden="true" /></button>
                  </div>
                </article>
              ))}
              {!visiblePapers.length && (
                <div className="empty-state">
                  <MagnifyingGlass aria-hidden="true" />
                  <h2>{text.noMatches}</h2>
                  <p>{text.noMatchesHint}</p>
                  <button className="secondary-button" onClick={() => { setQuery(""); setFilter("all"); setTagFilter(null); }}>{text.clearConditions}</button>
                </div>
              )}
            </div>
          </section>
        )}
      </section>

      {showRequest && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowRequest(false)}>
          <section className="request-modal" role="dialog" aria-modal="true" aria-labelledby="request-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className="modal-close" type="button" onClick={() => setShowRequest(false)} title={text.close}><X aria-hidden="true" /></button>
            <span className="modal-icon"><ChatCircleDots weight="duotone" aria-hidden="true" /></span>
            <p className="eyebrow">{text.connector}</p>
            <h2 id="request-title">{text.requestTranslation}</h2>
            <p>{text.requestHelp}</p>
            <label><span>{text.arxivId}</span><input autoFocus value={arxivDraft} onChange={(event) => setArxivDraft(event.target.value)} placeholder={text.arxivExample} /></label>
            <div className="prompt-preview">{text.translationPrompt(arxivDraft.trim() || "2510.09023v1")}</div>
            <button className="primary-button wide" type="button" onClick={() => void copyRequest()}>{text.copyRequest}</button>
          </section>
        </div>
      )}
    </main>
  );
}
