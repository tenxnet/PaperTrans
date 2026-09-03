type PaperReviewState = Readonly<{
  status: string;
  qa: Readonly<{ status: string }>;
}>;

/** Distinguish an in-progress missing QA report from a finalized QA gap. */
export function paperNeedsReview(paper: PaperReviewState): boolean {
  return paper.status === "needs_review"
    || paper.status === "failed"
    || paper.qa.status === "failed"
    || (paper.status === "completed" && paper.qa.status === "missing");
}
