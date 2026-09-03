import assert from "node:assert/strict";
import test from "node:test";

import { paperNeedsReview } from "../lib/paper-review.ts";

function paper(status, qaStatus) {
  return { status, qa: { status: qaStatus } };
}

test("completed papers require review unless QA passed", () => {
  assert.equal(paperNeedsReview(paper("completed", "passed")), false);
  assert.equal(paperNeedsReview(paper("completed", "failed")), true);
  assert.equal(paperNeedsReview(paper("completed", "missing")), true);
});

test("in-progress jobs do not require review only because QA is not ready", () => {
  assert.equal(paperNeedsReview(paper("prepared", "missing")), false);
  assert.equal(paperNeedsReview(paper("translating", "missing")), false);
});

test("explicit review and failure states always require review", () => {
  assert.equal(paperNeedsReview(paper("needs_review", "passed")), true);
  assert.equal(paperNeedsReview(paper("failed", "missing")), true);
});
