import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("PWA manifest points to the SchoolHop app shell", async () => {
  const manifest = JSON.parse(await read("app/static/manifest.webmanifest"));

  assert.equal(manifest.name, "SchoolHop");
  assert.equal(manifest.start_url, "/");
  assert.equal(manifest.display, "standalone");
  assert.ok(manifest.icons.some((icon) => icon.src === "/static/icon.svg"));
});

test("service worker does not cache API or non-GET requests", async () => {
  const worker = await read("app/static/service-worker.js");

  assert.match(worker, /url\.pathname\.startsWith\("\/api\/"\)/);
  assert.match(worker, /event\.request\.method !== "GET"/);
  assert.match(worker, /return;/);
});

test("registration UI uses verified account creation flow", async () => {
  const html = await read("app/static/index.html");
  const app = await read("app/static/app.js");

  assert.match(html, /Send verification code/);
  assert.match(html, /Verify email/);
  assert.match(app, /\/api\/auth\/register\/start/);
  assert.match(app, /\/api\/auth\/register\/verify/);
  assert.match(app, /\/api\/auth\/register\/complete/);
  assert.doesNotMatch(app, /\/api\/auth\/register["']/);
});
