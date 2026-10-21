import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(new URL("../../app/static/app.js", import.meta.url), "utf8");
const section = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function harness() {
  let now = 1000000;
  let sequence = 0;
  const timers = new Map();
  const events = new Map();
  const calls = [];
  const trip = { id: "synthetic-trip", status: "started", started_at: new Date(now).toISOString(), children: [] };
  const state = { token: "synthetic-account", trips: [trip], locationPollId: null, lastViewerResumeAt: 0 };
  const window = {
    setInterval(fn, ms) { const id = ++sequence; timers.set(id, { fn, ms, interval: true }); return id; },
    clearInterval(id) { timers.delete(id); },
    setTimeout(fn, ms) { const id = ++sequence; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener: (event, handler) => events.set(event, handler),
  };
  const document = { hidden: false, addEventListener: (event, handler) => events.set(event, handler) };
  const context = vm.createContext({
    state, window, document, performance: { now: () => now },
    Date: class extends Date { static now() { return now; } },
    nativeConfig: {}, console, selectedTrip: () => trip,
    renderHome() {}, renderTripConsole() {}, renderAll() {},
    refreshGroupContext: async () => { calls.push("group"); },
    reportClientError: () => calls.push("error"), shouldFetchRoute: () => false,
    escapeHTML: (text) => String(text),
    api: async (path) => { calls.push(path); return { location: null }; },
  });
  vm.runInContext([
    section("function tripIsActive(", "function canEndTrip("),
    section("function mapLocation(", "function formatClock("),
    section("function handoverButtonsHTML(", "function renderSwapRequestPanel("),
    section("function viewerIsVisible(", "function shouldFetchRoute("),
    section("function updateTripLocation(", "function updateTripRoute("),
  ].join("\n"), context);
  return { context, state, trip, calls, timers, events, document, advance: (ms) => { now += ms; } };
}

test("active viewers poll every ten seconds, pause when hidden and refresh immediately on return", async () => {
  const h = harness();
  h.context.wireViewerEvents();
  h.context.reconcileLocationPolling(h.trip);
  await flush();
  assert.equal(h.calls.filter((call) => call.endsWith("/location")).length, 1);
  assert.equal([...h.timers.values()].find((timer) => timer.interval).ms, 10000);
  h.document.hidden = true;
  h.events.get("visibilitychange")();
  assert.equal(h.timers.size, 0);
  h.document.hidden = false;
  h.events.get("visibilitychange")();
  await flush();
  assert.equal(h.calls.filter((call) => call.endsWith("/location")).length, 2);
  assert.ok(h.calls.includes("group"));
  h.events.get("pageshow")(); // Coalesce duplicate lifecycle events.
  await flush();
  assert.equal(h.calls.filter((call) => call.endsWith("/location")).length, 2);
});

test("a suspended response cannot overwrite a fresh foreground response", async () => {
  const h = harness();
  let release;
  h.context.api = () => new Promise((resolve) => { release = resolve; });
  h.context.startLocationPolling(h.trip.id);
  await flush();
  h.context.stopLocationPolling();
  h.context.api = async () => ({ location: { latitude: 2, longitude: 3, fresh: true } });
  h.context.startLocationPolling(h.trip.id);
  await flush();
  release({ location: { latitude: 0, longitude: 0 } });
  await flush();
  assert.equal(h.trip.latest_location.latitude, 2);
  assert.equal(h.state.locationPollInFlight, null);
});

test("same coordinates with a newer capture timestamp still refresh freshness", () => {
  const h = harness();
  const location = { latitude: 1, longitude: 2, recorded_at: new Date(1000000).toISOString(), fresh: true, age_seconds: 0 };
  h.context.updateTripLocation(h.trip.id, location, false);
  assert.equal(h.context.mapLocation(h.trip).fresh, true);
  h.advance(61000);
  assert.equal(h.context.mapLocation(h.trip).fresh, false);
  assert.match(h.context.mapLocation(h.trip).message, /last known/);
  h.context.updateTripLocation(h.trip.id, { ...location, recorded_at: new Date(1061000).toISOString() }, false);
  assert.equal(h.context.mapLocation(h.trip).fresh, true);
});

test("missing location clears an old marker; missing timestamps never imply freshness", async () => {
  const h = harness();
  h.trip.latest_location = { latitude: 1, longitude: 2 };
  assert.equal(h.context.mapLocation(h.trip).fresh, false);
  h.context.startLocationPolling(h.trip.id);
  await flush();
  assert.equal(h.trip.latest_location, null);
});

test("GPS polling recovers from a hung request and keeps updates single-flight", async () => {
  const h = harness();
  h.context.api = () => new Promise(() => {});
  h.context.startLocationPolling(h.trip.id);
  const request = h.state.locationPollInFlight;
  await h.context.fetchLatestLocation(h.trip.id);
  assert.equal(h.state.locationPollInFlight, request);
  [...h.timers.values()].find((timer) => timer.ms === 12000).fn();
  await flush();
  assert.equal(h.state.locationPollInFlight, null);
  assert.ok(h.calls.includes("error"));
  h.context.api = async () => ({ location: { latitude: 3, longitude: 4 } });
  [...h.timers.values()].find((timer) => timer.interval).fn();
  await flush();
  assert.equal(h.trip.latest_location.latitude, 3);
});

test("delayed trips retain GPS only after starting", async () => {
  const h = harness();
  h.trip.status = "delayed";
  assert.equal(h.context.tripIsActive(h.trip), true);
  h.context.reconcileLocationPolling(h.trip);
  await flush();
  assert.notEqual(h.state.locationPollId, null);
  h.trip.started_at = null;
  h.context.reconcileLocationPolling(h.trip);
  assert.equal(h.state.locationPollId, null);
});

test("handover controls match trip type and disable completed actions", () => {
  const h = harness();
  h.trip.children = [{ child_id: "synthetic-child", name: "Test", pickup_status: "pending", dropoff_status: "pending" }];
  for (const [type, action, other] of [["pickup", "picked_up", "dropped_off"], ["dropoff", "dropped_off", "picked_up"]]) {
    h.trip.trip_type = type;
    const html = h.context.handoverButtonsHTML(h.trip, true);
    assert.ok(html.includes(`data-type="${action}"`));
    assert.ok(!html.includes(`data-type="${other}"`));
    const parentHtml = h.context.handoverButtonsHTML(h.trip, false);
    assert.match(parentHtml, new RegExp(`data-type="${action}" disabled`));
  }
  h.trip.children[0].dropoff_status = "dropped_off";
  assert.match(h.context.handoverButtonsHTML(h.trip, true), /data-type="dropped_off" disabled/);
});

test("stale GPS hides cached ETA and distance without changing child routing", () => {
  const h = harness();
  Object.assign(h.context, { escapeHTML: (s) => s, lastLocationLabel: () => "", routeStatusMessage: () => "Waiting" });
  const start = source.indexOf("function etaSummaryHTML(");
  const end = source.indexOf("\nfunction ", start + 1);
  vm.runInContext(source.slice(start, end), h.context);
  h.trip.latest_location = { latitude: 1, longitude: 2, fresh: false };
  h.trip.latest_route = { eta_at: "2099-01-01", duration_seconds: 100, distance_meters: 200 };
  const html = h.context.etaSummaryHTML(h.trip);
  assert.match(html, /GPS stale/);
  assert.ok(!html.includes("2099"));
});
