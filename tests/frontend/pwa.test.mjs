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

test("service worker caches the current app shell version", async () => {
  const html = await read("app/static/index.html");
  const worker = await read("app/static/service-worker.js");

  assert.match(html, /\/static\/app\.js\?v=24/);
  assert.match(worker, /schoolhop-shell-v24/);
  assert.match(worker, /\/static\/app\.js\?v=24/);
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

test("navigation, trip map, history, and driver actions match roster workflow", async () => {
  const html = await read("app/static/index.html");
  const app = await read("app/static/app.js");

  for (const tab of ["home", "group", "children", "roster", "trip", "alerts"]) {
    assert.match(html, new RegExp(`data-tab="${tab}"`));
  }
  assert.match(html, /id="showTripHistory"/);
  assert.match(html, /id="requestAccessForm"/);
  assert.match(html, /id="requestSchool"/);
  assert.match(html, /id="requestGroup"/);
  assert.match(html, /Request access/);
  assert.match(html, /Trip History/);
  assert.doesNotMatch(html, /id="rosterHistoryTripList"/);
  assert.match(html, /id="historyTripList"/);
  assert.match(html, /id="delayTrip"/);
  assert.match(html, /Report 10-minute delay/);
  assert.match(html, /id="swapTrip"/);
  assert.match(html, /id="tripMap"/);
  assert.match(app, /function isArchivedTrip/);
  assert.match(app, /async function loadTripHistory/);
  assert.match(app, /state\.history = await api\(`\/api\/groups\/\$\{groupId\}\/trips\/history`\)/);
  assert.doesNotMatch(app, /rosterHistoryTripList/);
  assert.match(app, /Invite token:/);
  assert.match(app, /Code:/);
  assert.match(app, /Access request sent to the group creator/);
  assert.match(app, /You have to leave your existing group first/);
  assert.match(app, /\/api\/groups\/discover/);
  assert.match(app, /function renderAccessRequestOptions/);
  assert.match(app, /function isTodayTrip/);
  assert.match(app, /function isActionableTrip/);
  assert.match(app, /function reportDelay/);
  assert.match(app, /function requestSwap/);
  assert.match(app, /Trips can only be started on their service date/);
  assert.match(app, /Accept this trip before starting it/);
  assert.match(app, /function renderTripMap/);
  assert.match(app, /function startLocationPolling/);
  assert.match(app, /\/api\/trips\/\$\{tripId\}\/location/);
  assert.match(app, /\/api\/trips\/\$\{tripId\}\/route/);
  assert.match(app, /ROUTE_REFRESH_MS = 30000/);
  assert.match(app, /function etaSummaryHTML/);
  assert.match(app, /ETA to School/);
  assert.match(app, /ETA temporarily unavailable/);
  assert.match(app, /vehicle-marker/);
  assert.match(app, /const startedTrips = trips\.filter\(\(trip\) => trip\.status === "started"\)/);
  assert.match(html, /Current Trip/);
  assert.match(html, /Next Trip/);
  assert.match(app, /data-tracking="\$\{tracking \? "on" : "off"\}"/);
  assert.match(app, /target\.closest\("\.trip-card\[data-tracking='on'\]"\)/);
});

test("home screen orders pending actions, current trip, next trip, and upcoming trips", async () => {
  const html = await read("app/static/index.html");
  const app = await read("app/static/app.js");

  const pendingIndex = html.indexOf('id="pendingActionPanel"');
  const currentIndex = html.indexOf('id="homeCurrentTripPanel"');
  const nextIndex = html.indexOf('id="homeNextTripPanel"');
  const upcomingIndex = html.indexOf('id="homeUpcomingPanel"');

  assert.ok(pendingIndex > -1);
  assert.ok(pendingIndex < currentIndex);
  assert.ok(currentIndex < nextIndex);
  assert.ok(nextIndex < upcomingIndex);
  assert.match(html, /<h2>Next Trip<\/h2>/);
  assert.doesNotMatch(html, /Next rosters/);
  assert.match(app, /api\("\/api\/pending-actions"\)/);
  assert.match(app, /state\.pendingActions = pendingActions/);
  assert.match(app, /\$\("pendingActionPanel"\)\.classList\.toggle\("hidden", !state\.pendingActions\.length\)/);
  assert.match(app, /const currentTrip = startedTrips\[0\] \|\| null/);
  assert.match(app, /const startedTrip = trips\.find\(\(trip\) => trip\.status === "started"\)/);
  assert.match(app, /const nextTrip = upcomingTrips\[0\] \|\| null/);
});

test("current trip cards render live ETA and unavailable states", async () => {
  const app = await read("app/static/app.js");
  const styles = await read("app/static/styles.css");

  assert.match(app, /etaSummaryHTML\(trip\)/);
  assert.match(app, /route && route\.eta_at && route\.duration_seconds != null/);
  assert.match(app, /Waiting for driver's location\.\.\./);
  assert.match(app, /if \(location && !location\.fresh\)/);
  assert.match(app, /Last location update:/);
  assert.match(styles, /\.eta-summary/);
});

test("home keeps trip rendering independent from non-critical refresh failures", async () => {
  const app = await read("app/static/app.js");

  assert.match(app, /Promise\.allSettled\(\[api\("\/api\/notifications"\), api\("\/api\/pending-actions"\)\]\)/);
  assert.match(app, /settledValue\(notificationsResult, state\.notifications, "notifications-refresh"\)/);
  assert.match(app, /settledValue\(pendingActionsResult, state\.pendingActions, "pending-actions-refresh"\)/);
  assert.match(app, /api\(`\/api\/groups\/\$\{groupId\}\/trips`\)/);
});

test("completed actions are removed from pending actions immediately", async () => {
  const app = await read("app/static/app.js");

  assert.match(app, /state\.pendingActions = state\.pendingActions\.filter\(\(action\) => action\.type !== "driver_assignment" \|\| action\.trip_id !== trip\.id\)/);
  assert.match(app, /state\.pendingActions = state\.pendingActions\.filter\(\(action\) => action\.type !== "swap_request" \|\| action\.id !== requestId\)/);
  assert.match(app, /state\.pendingActions = state\.pendingActions\.filter\(\(action\) => action\.type !== "invitation" \|\| action\.action_url !== `\/\?invite=\$\{token\}`\)/);
  assert.doesNotMatch(app, /function pendingActions/);
});

test("alerts can be deleted individually, swiped away, or cleared when read", async () => {
  const html = await read("app/static/index.html");
  const app = await read("app/static/app.js");

  assert.match(html, /id="clearReadNotifications"/);
  assert.match(app, /class="ghost danger notification-delete"/);
  assert.match(app, /api\(`\/api\/notifications\/\$\{notificationId\}`,[\s\S]*method: "DELETE"/);
  assert.match(app, /api\("\/api\/notifications\/read", \{ method: "DELETE" \}\)/);
  assert.match(app, /function onNotificationPointerDown/);
  assert.match(app, /deltaX < -70/);
});

test("new foreground alerts play an unlocked notification tone", async () => {
  const app = await read("app/static/app.js");

  assert.match(app, /knownNotificationIds: new Set\(\)/);
  assert.match(app, /function setNotifications/);
  assert.match(app, /state\.notificationIdsSeeded = true/);
  assert.match(app, /playNotificationSound\(newNotification\.id\)/);
  assert.match(app, /function unlockNotificationSound/);
  assert.match(app, /schoolhopNotificationSoundEnabled/);
  assert.match(app, /function notificationToneUrl/);
  assert.match(app, /new Audio\(notificationToneUrl\(\)\)/);
  assert.match(app, /document\.addEventListener\("pointerdown", unlockNotificationSound, \{ once: true \}\)/);
});

test("assignment response controls are final after accept or decline", async () => {
  const html = await read("app/static/index.html");
  const app = await read("app/static/app.js");

  assert.match(html, /id="tripResponseStatus"/);
  assert.match(html, /id="acceptTrip"[\s\S]*>Accept<\/button>/);
  assert.match(html, /id="declineTrip"[\s\S]*>Decline<\/button>/);
  assert.match(app, /function isPendingTripAssignment/);
  assert.match(app, /tripResponseStatus\(trip\) === "pending"/);
  assert.match(app, /\$\("acceptTrip"\)\.classList\.toggle\("hidden", !pendingAssignment\)/);
  assert.match(app, /\$\("declineTrip"\)\.classList\.toggle\("hidden", !pendingAssignment\)/);
  assert.match(app, /trip\.my_response = result/);
});

test("swap request actions only render while open and old deep links are read-only", async () => {
  const app = await read("app/static/app.js");

  assert.match(app, /request\.status === "open"[\s\S]*data-status="accepted"/);
  assert.match(app, /finalStatus \? `<div>\$\{pill\(formatStatus\(finalStatus\), statusTone\(finalStatus\)\)\}<\/div>`/);
  assert.match(app, /state\.pendingSwapRequestId = swapRequestId/);
  assert.match(app, /state\.pendingActions = pendingActions/);
});

test("automatic GPS updates are throttled with latest trailing position", async () => {
  const app = await read("app/static/app.js");

  assert.match(app, /queuedLocationPost: null/);
  assert.match(app, /function queueLatestPosition/);
  assert.match(app, /flushQueuedPosition/);
  assert.match(app, /postPosition\(trip\.id, position, \{ force: true \}\)/);
  assert.match(app, /state\.locationPostInFlight \|\| now - state\.lastLocationPostAt < 4000/);
});
