import assert from "node:assert/strict";
import test from "node:test";

import {
  isArtifactManifestPublishable,
  SAFE_ARXIV_RENDERER_GENERATION,
} from "../lib/artifact-security.ts";

test("publishes only the current safe arXiv renderer generation", () => {
  assert.equal(SAFE_ARXIV_RENDERER_GENERATION, "3");
  assert.equal(isArtifactManifestPublishable({
    status: "completed",
    sourceType: "arxiv",
    rendererVersion: "3-deadbeefcafe",
  }), true);
  for (const rendererVersion of [
    undefined,
    "2-deadbeefcafe",
    "3-short",
    "3-DEADBEEFCAFE",
  ]) {
    assert.equal(isArtifactManifestPublishable({
      status: "completed",
      sourceType: "arxiv",
      rendererVersion,
    }), false);
  }
});

test("preserves PDF publication rules", () => {
  assert.equal(isArtifactManifestPublishable({
    status: "completed",
    sourceType: "pdf",
    provider: "chatgpt",
  }), true);
  assert.equal(isArtifactManifestPublishable({
    status: "needs_review",
    sourceType: "pdf",
    provider: "none",
  }), false);
});

test("rejects incomplete artifacts regardless of source", () => {
  assert.equal(isArtifactManifestPublishable({
    status: "translating",
    sourceType: "pdf",
  }), false);
});
