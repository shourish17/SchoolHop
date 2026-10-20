import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(new URL("../../app/static/app.js", import.meta.url), "utf8");

function harness(platform, watchId = null) {
  const calls = [];
  const state = { watchId, trackingTripId: null, locationPostTimer: null };
  const context = vm.createContext({
    state, isNativeApp: platform !== "browser", nativePlatform: () => platform,
    selectedTrip: () => ({ id: "trip-A" }), toast: (message) => calls.push(message),
    nativePlugins: () => ({ SchoolHopLocation: {
      status: async () => ({ tracking: true, tripId: "trip-A" }),
      requestLocationUpdate: async () => calls.push("native-fix"),
    } }),
    stopNativeTracking: async () => calls.push("native-stop"),
    navigator: { geolocation: {
      clearWatch: () => calls.push("browser-stop"),
      getCurrentPosition: async (success) => success({ coords: {} }),
    } },
    postPosition: async () => calls.push("web-upload"), renderTripConsole() {},
    clearLocalTrackingState: () => { state.watchId = null; calls.push("local-stop"); },
    reportClientError() {}, window: { clearTimeout() {} },
  });
  vm.runInContext(source.slice(source.indexOf("function stopTracking()"), source.indexOf("function reconcileLocationPolling(")), context);
  vm.runInContext(source.slice(source.indexOf("async function sendCurrentLocation("), source.indexOf("async function postPosition(")), context);
  return { context, calls, state };
}

test("Android Sign Out stops native GPS even after WebView tracking state was lost", () => {
  const { context, calls } = harness("android");
  context.stopTracking();
  assert.deepEqual(calls, ["native-stop"]);
});

test("Android Sign Out waits for native shutdown before clearing local tracking", async () => {
  const { context, calls, state } = harness("android", "native");
  let release;
  context.stopNativeTracking = () => new Promise((resolve) => { release = resolve; });
  const stopped = context.stopTrackingForLogout();
  assert.equal(state.watchId, "native");
  release(); await stopped;
  assert.equal(state.watchId, null);
  assert.deepEqual(calls, ["local-stop"]);
});

test("failed Android shutdown preserves the session's tracking state for retry", async () => {
  const { context, calls, state } = harness("android", "native");
  context.stopNativeTracking = async () => { throw new Error("unavailable"); };
  await assert.rejects(context.stopTrackingForLogout(), /unavailable/);
  assert.equal(state.watchId, "native");
  assert.deepEqual(calls, []);
});

test("Android manual GPS shares the native upload queue and never uploads through the WebView", async () => {
  const { context, calls } = harness("android");
  await context.sendCurrentLocation(false);
  assert.deepEqual(calls, ["native-fix"]);
});

for (const platform of ["ios", "browser"]) {
  test(`${platform} manual GPS preserves the existing WebView behavior`, async () => {
    const { context, calls } = harness(platform);
    await context.sendCurrentLocation(false);
    assert.deepEqual(calls, ["web-upload"]);
  });
}

test("Android cannot manually send GPS for a different trip", async () => {
  const { context, calls } = harness("android");
  context.selectedTrip = () => ({ id: "other-trip" });
  await assert.rejects(context.sendCurrentLocation(false), /Start GPS sharing/);
  assert.deepEqual(calls, []);
});
