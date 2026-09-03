import assert from "node:assert/strict";
import test from "node:test";

import { normalizeArxivId } from "../lib/arxiv-id.ts";

test("normalizes complete arXiv identifiers and official URLs", () => {
  const validInputs = new Map([
    ["2508.19843", "2508.19843"],
    ["arXiv:2508.19843V3", "2508.19843v3"],
    ["https://arxiv.org/abs/math/0211159", "math/0211159"],
    ["https://arxiv.org/html/2508.19843v3", "2508.19843v3"],
    ["https://arxiv.org/pdf/2508.19843.pdf", "2508.19843"],
  ]);
  for (const [input, expected] of validInputs) {
    assert.equal(normalizeArxivId(input), expected, input);
  }
});

test("rejects identifiers embedded in untrusted input", () => {
  for (const input of [
    "12508.19843",
    "2599.19843",
    `2508.19843v${"1".repeat(80)}`,
    `${"a".repeat(40)}/0211159`,
    "math/٠٢١١١٥٩",
    "٢٥08.19843",
    "2508.19843v1١",
    "not-arxiv-2508.19843-extra",
    "https://arxiv.org.evil.example/abs/2508.19843",
    "https://user@arxiv.org/abs/2508.19843",
    "http://arxiv.org/abs/2508.19843",
    "https://arxiv.org:444/abs/2508.19843",
    "https://example.com/?id=2508.19843",
  ]) {
    assert.equal(normalizeArxivId(input), null, input);
  }
});

test("every accepted identifier produces a valid default job ID", () => {
  for (const input of [
    "2508.19843v12345",
    `${"a".repeat(32)}/0211159v12345`,
  ]) {
    const identifier = normalizeArxivId(input);
    assert.notEqual(identifier, null);
    const jobId = `arxiv-${identifier.replace("/", "-")}-mcp`;
    assert.match(jobId, /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/);
  }
});

test("rejects non-string input", () => {
  assert.equal(normalizeArxivId(null), null);
  assert.equal(normalizeArxivId({}), null);
});
