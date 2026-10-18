import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";

const require = createRequire(import.meta.url);
const { validateFirebaseConfig } = require("../../scripts/configure-android-firebase.js");
const source = await readFile(new URL("../../app/static/app.js", import.meta.url), "utf8");

test("Firebase staging accepts only the registered project and Android client", () => {
  const config = {
    project_info: { project_id: "schoolhop-1c00d", project_number: "123" },
    client: [{ client_info: { android_client_info: { package_name: "com.schoolhop.app" }, mobilesdk_app_id: "test-app" }, api_key: [{ current_key: "test-key" }] }],
  };
  assert.doesNotThrow(() => validateFirebaseConfig(JSON.stringify(config)));
  config.project_info.project_id = "other-project";
  assert.throws(() => validateFirebaseConfig(JSON.stringify(config)), /must match/);
  config.project_info.project_id = "schoolhop-1c00d";
  config.client[0].client_info.android_client_info.package_name = "other.package";
  assert.throws(() => validateFirebaseConfig(JSON.stringify(config)), /must match/);
  assert.throws(() => validateFirebaseConfig("private-canary invalid JSON"), (error) => !error.message.includes("private-canary"));
});

function pushHarness({ configured = true, granted = true } = {}) {
  const listeners = new Map();
  const storage = new Map();
  const calls = [];
  const state = { token: "account-A", nativePushSigningOut: false };
  const plugin = {
    async createChannel(channel) { calls.push(["channel", channel]); },
    async requestPermissions() { return { receive: granted ? "granted" : "denied" }; },
    async checkPermissions() { return { receive: granted ? "granted" : "denied" }; },
    async addListener(event, handler) { listeners.set(event, handler); },
    async register() { calls.push(["native-register"]); },
    async unregister() { calls.push(["native-unregister"]); },
    async removeAllDeliveredNotifications() { calls.push(["clear-notifications"]); },
  };
  const context = vm.createContext({
    state, isNativeApp: true, nativePlugins: () => ({ PushNotifications: plugin }), nativePlatform: () => "android",
    androidFirebaseMessagingConfigured: async () => configured,
    reportClientError: () => {},
    localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    api: async (path, options) => calls.push([options.method, JSON.parse(options.body), options.headers?.Authorization || `Bearer ${state.token}`, path]),
  });
  vm.runInContext(`let nativePushQueue = Promise.resolve(); let nativePushRegistration = null;\n${source.slice(source.indexOf("async function registerNativePushToken("), source.indexOf("async function androidFirebaseMessagingConfigured("))}`, context);
  return { context, state, plugin, listeners, storage, calls };
}

test("push safely skips Firebase-free builds and denied permission", async () => {
  for (const options of [{ configured: false }, { granted: false }]) {
    const h = pushHarness(options);
    await h.context.registerNativePushToken();
    assert.ok(!h.calls.some(([kind]) => kind === "native-register"));
    assert.equal(h.listeners.size, 0);
  }
});

test("token refresh registers the new token, removes the old token and signs out in order", async () => {
  const h = pushHarness();
  await h.context.registerNativePushToken();
  await h.listeners.get("registration")({ value: "token-original" });
  await h.listeners.get("registration")({ value: "token-refreshed" });
  await h.context.unregisterNativePushToken();
  const requests = h.calls.filter(([kind]) => ["POST", "DELETE"].includes(kind));
  assert.deepEqual(requests.map(([method, device]) => [method, device.token]), [
    ["POST", "token-original"], ["POST", "token-refreshed"], ["DELETE", "token-original"], ["DELETE", "token-refreshed"],
  ]);
  assert.ok(requests.every((row) => row[2] === "Bearer account-A"));
  assert.equal(h.storage.size, 0);
  assert.deepEqual(h.calls.slice(-2).map(([kind]) => kind), ["native-unregister", "clear-notifications"]);
  await h.listeners.get("registration")({ value: "late-token" });
  assert.equal(h.calls.filter(([kind]) => kind === "POST").length, 2);
});

test("Sign Out waits for an in-flight registration and cleans up the same account", async () => {
  const h = pushHarness();
  await h.context.registerNativePushToken();
  let release;
  const started = new Promise((resolve) => { release = resolve; });
  const originalApi = h.context.api;
  h.context.api = async (...args) => { if (args[1].method === "POST") await started; return originalApi(...args); };
  const registering = h.listeners.get("registration")({ value: "token-in-flight" });
  const signingOut = h.context.unregisterNativePushToken();
  release();
  await Promise.all([registering, signingOut]);
  assert.deepEqual(h.calls.filter(([kind]) => ["POST", "DELETE"].includes(kind)).map(([method]) => method), ["POST", "DELETE"]);
});

test("failed device removal keeps Sign Out retryable", async () => {
  const h = pushHarness();
  await h.context.registerNativePushToken();
  await h.listeners.get("registration")({ value: "saved-token" });
  h.context.api = async () => { throw new Error("offline"); };
  await assert.rejects(h.context.unregisterNativePushToken(), /offline/);
  assert.equal(h.state.nativePushSigningOut, false);
  assert.ok(h.storage.has("schoolhop_push_devices"));
  assert.ok(!h.calls.some(([kind]) => kind === "native-unregister"));
});

test("a failed refresh keeps the previous token available for Sign Out cleanup", async () => {
  const h = pushHarness();
  await h.context.registerNativePushToken();
  await h.listeners.get("registration")({ value: "old-token" });
  const originalApi = h.context.api;
  h.context.api = async () => { throw new Error("offline"); };
  await h.listeners.get("registration")({ value: "new-token" });
  h.context.api = originalApi;
  await h.context.unregisterNativePushToken();
  assert.deepEqual(h.calls.filter(([kind]) => kind === "DELETE").map(([, device]) => device.token), ["old-token", "new-token"]);
});

test("account switching registers the current token for the new account", async () => {
  const h = pushHarness();
  await h.context.registerNativePushToken();
  await h.listeners.get("registration")({ value: "first-token" });
  await h.context.unregisterNativePushToken();
  h.state.token = "account-B";
  h.state.nativePushSigningOut = false;
  await h.context.registerNativePushToken();
  await h.listeners.get("registration")({ value: "new-account-token" });
  const requests = h.calls.filter(([kind]) => kind === "POST");
  assert.equal(requests.at(-1)[2], "Bearer account-B");
  assert.equal(h.listeners.size, 2);
});

test("notification routes survive login and reject untrusted URLs", async () => {
  const state = { me: null };
  let refreshes = 0;
  const context = vm.createContext({
    URL, state, window: { location: { origin: "https://localhost" } }, nativeApiBaseUrl: "https://schoolhop.shourish.com",
    localStorage: { setItem() {} }, document: { querySelector: () => null }, $: () => null,
    rememberSelectedTrip: (trip) => { state.selectedTripId = trip; },
    refreshGroupContext: async () => { refreshes++; }, setTab: () => {},
  });
  vm.runInContext(source.slice(source.indexOf("async function openActionUrl("), source.indexOf("function wireNativeEvents(")), context);
  await context.openActionUrl("https://evil.example/?trip=wrong&tab=trip");
  assert.equal(state.selectedTripId, undefined);
  await context.openActionUrl("/?tab=trip&trip=trip-id&swap_request=swap-id");
  assert.equal(state.selectedTripId, "trip-id");
  assert.equal(state.pendingSwapRequestId, "swap-id");
  assert.equal(state.pendingTab, "trip");
  assert.equal(refreshes, 0);
  state.me = { id: "account-A" };
  await context.openActionUrl("https://schoolhop.shourish.com/?tab=roster&trip=trip-id");
  assert.equal(refreshes, 1);
  await context.openActionUrl("/?invite=invitation-id");
  assert.equal(state.pendingInviteToken, "invitation-id");
  assert.equal(state.pendingTab, "group");
});
