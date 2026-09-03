import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  isAllowedLoopbackHost,
  validateLocalMutationRequest,
} from "../lib/local-http-boundary.ts";

function mutationRequest({
  url = "http://127.0.0.1:3000/api/jobs",
  host = "127.0.0.1:3000",
  origin,
  fetchSite,
  contentType = "application/json",
  forwardedHost,
} = {}) {
  const headers = new Headers();
  if (host !== null) headers.set("host", host);
  if (origin !== undefined) headers.set("origin", origin);
  if (fetchSite !== undefined) headers.set("sec-fetch-site", fetchSite);
  if (contentType !== null) headers.set("content-type", contentType);
  if (forwardedHost !== undefined) headers.set("x-forwarded-host", forwardedHost);
  return new Request(url, { method: "POST", headers, body: "{}" });
}

async function routeFiles(root) {
  const files = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const candidate = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...await routeFiles(candidate));
    else if (entry.name === "route.ts") files.push(candidate);
  }
  return files;
}

test("accepts only literal loopback Host authorities", () => {
  for (const host of [
    "127.0.0.1",
    "127.0.0.1:3000",
    "localhost",
    "LOCALHOST:65535",
    "[::1]",
    "[::1]:3000",
  ]) assert.equal(isAllowedLoopbackHost(host), true, host);

  for (const host of [
    null,
    "",
    "attacker.example",
    "attacker.example:3000",
    "127.0.0.2:3000",
    "localhost.attacker.example",
    "127.0.0.1:0",
    "127.0.0.1:65536",
    "127.0.0.1:3000, attacker.example",
  ]) assert.equal(isAllowedLoopbackHost(host), false, String(host));
});

test("allows the same-origin Web client and explicit JSON media type", () => {
  const result = validateLocalMutationRequest(mutationRequest({
    origin: "http://127.0.0.1:3000",
    fetchSite: "same-origin",
    contentType: "application/json; charset=utf-8",
  }), ["application/json"]);
  assert.deepEqual(result, { ok: true });
});

test("preserves local non-browser clients that omit browser provenance headers", () => {
  const result = validateLocalMutationRequest(
    mutationRequest(),
    ["application/json"],
  );
  assert.deepEqual(result, { ok: true });
});

test("rejects cross-site Fetch Metadata and cross-origin Origin", () => {
  for (const request of [
    mutationRequest({ origin: "https://attacker.example", fetchSite: "cross-site" }),
    mutationRequest({ origin: "https://attacker.example" }),
    mutationRequest({ origin: "http://localhost:3000", fetchSite: "same-origin" }),
    mutationRequest({ origin: "http://127.0.0.1:4000", fetchSite: "same-origin" }),
    mutationRequest({ origin: "http://user@127.0.0.1:3000", fetchSite: "same-origin" }),
    mutationRequest({ origin: "http://127.0.0.1:3000/path", fetchSite: "same-origin" }),
    mutationRequest({ origin: "null", fetchSite: "same-origin" }),
  ]) {
    const result = validateLocalMutationRequest(request, ["application/json"]);
    assert.equal(result.ok, false);
    assert.equal(result.status, 403);
  }
});

test("rejects browser-safelisted and missing content types", () => {
  for (const contentType of [null, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"]) {
    const result = validateLocalMutationRequest(mutationRequest({
      origin: "http://127.0.0.1:3000",
      fetchSite: "same-origin",
      contentType,
    }), ["application/json"]);
    assert.equal(result.ok, false);
    assert.equal(result.status, 415);
  }
});

test("accepts same-origin multipart imports only at multipart call sites", () => {
  const request = mutationRequest({
    url: "http://localhost:3000/api/papers/import",
    host: "localhost:3000",
    origin: "http://localhost:3000",
    fetchSite: "same-origin",
    contentType: "multipart/form-data; boundary=papertrans",
  });
  assert.deepEqual(
    validateLocalMutationRequest(request, ["multipart/form-data"]),
    { ok: true },
  );
});

test("Forwarded host spoofing cannot authorize a hostile raw Host", () => {
  const blocked = validateLocalMutationRequest(mutationRequest({
    url: "http://attacker.example/api/jobs",
    host: "attacker.example",
    origin: "http://attacker.example",
    fetchSite: "same-origin",
    forwardedHost: "127.0.0.1:3000",
  }), ["application/json"]);
  assert.equal(blocked.ok, false);
  assert.equal(blocked.status, 403);

  const allowed = validateLocalMutationRequest(mutationRequest({
    origin: "http://127.0.0.1:3000",
    fetchSite: "same-origin",
    forwardedHost: "attacker.example",
  }), ["application/json"]);
  assert.deepEqual(allowed, { ok: true });
});

test("the global proxy Host predicate is independent of Forwarded headers", () => {
  assert.equal(isAllowedLoopbackHost("attacker.example"), false);
  assert.equal(isAllowedLoopbackHost("127.0.0.1:3000"), true);
});

test("every Web API mutation route applies the local mutation boundary", async () => {
  for (const filename of await routeFiles(path.resolve("app/api"))) {
    const source = await readFile(filename, "utf8");
    if (!/export async function (?:POST|PUT|PATCH|DELETE)\b/.test(source)) continue;
    assert.match(source, /validateLocalMutationRequest\s*\(/, filename);
  }
});
