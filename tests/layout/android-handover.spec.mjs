import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

/* global state, refreshGroupContext, renderAll, onHandoverClick -- App globals evaluated inside the page. */

async function openTrip(page, { platform = "android", type = "pickup", parent = false, siblings = false, holdPost = false, failDetail = false, failPost = false } = {}) {
  const requests = [];
  const cacheHeaders = [];
  let releasePost;
  const gate = new Promise((resolve) => { releasePost = resolve; });
  const column = type === "pickup" ? "pickup_status" : "dropoff_status";
  const children = [{ child_id: "child-a", parent_id: "parent-a", name: "Child A", pickup_status: "pending", dropoff_status: "pending" }];
  if (siblings) children.push(
    { ...children[0], child_id: "sibling-a", name: "Sibling A" },
    { ...children[0], child_id: "child-b", parent_id: "parent-b", name: "Child B" },
  );
  const trip = { id: "test-trip", group_id: "test-group", driver_user_id: "driver", created_by: "driver",
    driver: { id: "driver", name: "Driver" }, trip_type: type, service_date: new Date().toISOString().slice(0, 10),
    expected_time: "08:00:00", status: "started", started_at: new Date().toISOString(), my_response: { status: "accepted" }, children,
    latest_location: { latitude: 51, longitude: 0, fresh: true, age_seconds: 0, recorded_at: new Date().toISOString() } };
  await page.addInitScript(({ platform }) => {
    localStorage.setItem("schoolhop_token", "synthetic-account");
    sessionStorage.setItem("schoolhop_selected_trip", "test-trip");
    window.gpsStops = 0;
    window.Capacitor = { isNativePlatform: () => true, getPlatform: () => platform, Plugins: {
      SchoolHopLocation: { stopTripTracking: async () => { window.gpsStops++; } },
    } };
  }, { platform });
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/")) {
      cacheHeaders.push(route.request().headers()["cache-control"]);
      let json = [];
      if (path === "/api/config") json = {};
      if (path === "/api/me") json = { user: { id: parent ? "parent-a" : "driver", email: "synthetic@example.test" } };
      if (path === "/api/groups") json = [{ id: "test-group", name: "Test Group" }];
      if (path === "/api/groups/test-group") json = { group: { id: "test-group", created_by: "driver" }, members: [], invitations: [], approvals: [] };
      if (path === "/api/groups/test-group/trips") json = [trip];
      if (path === "/api/trips/test-trip") {
        if (failDetail && requests.length) return route.fulfill({ status: 503, json: { detail: "Synthetic refresh failure" } });
        json = trip;
      }
      if (path.endsWith("/location")) json = { location: trip.latest_location };
      if (path.endsWith("/route")) json = { route: null };
      if (path.endsWith("/handover")) {
        const action = route.request().postDataJSON().handover_type;
        requests.push({ path, action });
        if (holdPost) await gate;
        if (failPost === true) return route.fulfill({ status: 409, json: { detail: "Synthetic rejected handover" } });
        const child = children.find((c) => path.includes(`/children/${c.child_id}/`));
        child[column] = action;
        if (failPost === "after") return route.fulfill({ status: 503, json: { detail: "Synthetic error after commit" } });
        json = { id: "test-event", child_id: child.child_id, handover_type: action };
      }
      return route.fulfill({ json });
    }
    const files = { "/": "index.html", "/static/app.js": "app.js", "/static/styles.css": "styles.css" };
    if (!files[path]) return route.abort();
    return route.fulfill({ body: await readFile(new URL(`../../app/static/${files[path]}`, import.meta.url)),
      contentType: path.endsWith(".js") ? "text/javascript" : path.endsWith(".css") ? "text/css" : "text/html" });
  });
  await page.goto("https://schoolhop.test/?tab=trip&trip=test-trip");
  await expect(page.locator('#handoverButtons [data-child="child-a"]')).toHaveCount(2);
  return { requests, trip, children, column, releasePost, cacheHeaders };
}

for (const [type, action, other, label] of [["pickup", "picked_up", "dropped_off", "Picked Up"], ["dropoff", "dropped_off", "picked_up", "Dropped Off"]]) {
  for (const submitted of [action, "absent"]) {
    test(`Android ${type} ${submitted}: correct actions, persisted completion and ongoing driver GPS`, async ({ page }) => {
      const h = await openTrip(page, { type });
      await expect(page.locator(`#handoverButtons [data-type="${action}"]`)).toHaveText(label);
      await expect(page.locator(`#handoverButtons [data-type="${other}"]`)).toHaveCount(0);
      await page.evaluate(() => { state.trackingTripId = "test-trip"; state.watchId = "native"; });
      await page.locator(`#handoverButtons [data-type="${submitted}"]`).click();
      await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(0);
      expect(await page.evaluate(() => state.trips[0].children[0].parent_id)).toBe("parent-a");
      expect(h.requests.map((r) => r.action)).toEqual([submitted]);
      expect(await page.evaluate(() => ({ stops: window.gpsStops, tracking: state.trackingTripId }))).toEqual({ stops: 0, tracking: "test-trip" });
      await page.evaluate(() => refreshGroupContext());
      await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(0);
      await page.reload();
      await expect(page.locator("#handoverButtons button")).toHaveCount(2);
      await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(0);
      expect(h.requests).toHaveLength(1);
      expect(await page.evaluate(() => navigator.serviceWorker.controller)).toBeNull();
    });
  }

  test(`Android ${type} locks both actions across GPS renders and concurrent taps`, async ({ page }) => {
    const h = await openTrip(page, { type, siblings: true, holdPost: true });
    await page.locator(`#handoverButtons [data-child="child-a"][data-type="${action}"]`).click();
    await expect.poll(() => h.requests.length).toBe(1);
    await page.evaluate(() => renderAll()); // The regular GPS renderer replaces the buttons.
    await expect(page.locator('#handoverButtons [data-child="child-a"]:enabled')).toHaveCount(0);
    await expect(page.locator('#handoverButtons [data-child="child-b"]:enabled')).toHaveCount(2);
    await page.evaluate(() => onHandoverClick({ target: document.querySelector('#handoverButtons [data-child="child-a"][data-type="absent"]') }));
    expect(h.requests).toHaveLength(1);
    h.releasePost();
    await expect(page.locator('#handoverButtons [data-child="child-a"]:enabled')).toHaveCount(0);
  });

  test(`Android ${type} retains acknowledged completion if detail refresh fails or stale state arrives`, async ({ page }) => {
    const h = await openTrip(page, { type, failDetail: true });
    await page.locator(`#handoverButtons [data-type="${action}"]`).click();
    await expect.poll(() => h.requests.length).toBe(1);
    await expect(page.locator("#toast")).toHaveText("Handover recorded");
    h.children[0][h.column] = "pending"; // Simulate a pre-POST group snapshot arriving late.
    await page.evaluate(() => refreshGroupContext());
    await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(0);
    await expect(page.locator("#handoverButtons")).toContainText(action);
    await expect(page.locator(".trip-card").first()).toContainText(action);
  });

  test(`Android ${type} hides only the completed parent's map and ETA; siblings continue`, async ({ page }) => {
    const h = await openTrip(page, { type, parent: true, siblings: true });
    await expect(page.locator("#tripMap iframe")).toHaveCount(1);
    h.children[0][h.column] = action;
    await page.evaluate(() => refreshGroupContext());
    await expect(page.locator("#tripMap iframe")).toHaveCount(1); // Own sibling remains pending.
    h.children[1][h.column] = "absent";
    await page.evaluate(() => refreshGroupContext());
    await expect(page.locator("#tripMap iframe")).toHaveCount(0);
    await expect(page.locator("#tripMap")).toContainText("Handover completed");
    await expect(page.locator("#trackingBanner")).toContainText("Handover completed");
    await expect(page.locator('.trip-card[data-tracking="on"]')).toHaveCount(0);
    expect(await page.evaluate(() => state.locationPollId)).toBeNull();
    expect(h.children[2][h.column]).toBe("pending"); // Another parent's child stays on the trip.
    expect(await page.evaluate(() => window.gpsStops)).toBe(0);
    await page.evaluate(() => { state.me.id = "parent-b"; renderAll(); });
    await expect(page.locator("#tripMap iframe")).toHaveCount(1);
    await page.reload();
    await expect(page.locator("#tripMap")).toContainText("Handover completed");
    await expect(page.locator("#tripMap iframe")).toHaveCount(0);
  });
}

test("Android API requests carry no-store through the native bridge; service worker is unused", async ({ page }) => {
  const h = await openTrip(page);
  expect(h.cacheHeaders.length).toBeGreaterThan(0);
  expect(h.cacheHeaders.every((header) => header === "no-store")).toBe(true);
  expect(await page.evaluate(() => navigator.serviceWorker.controller)).toBeNull();
});

test("Android rejected POST reconciles server state and allows a deliberate retry", async ({ page }) => {
  const h = await openTrip(page, { failPost: true });
  await page.locator('#handoverButtons [data-type="picked_up"]').click();
  await expect(page.locator("#toast")).toHaveText("Synthetic rejected handover");
  await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(2);
  expect(h.children[0].pickup_status).toBe("pending");
  expect(h.requests).toHaveLength(1);
});

test("Android reconciles a committed handover after a lost POST response", async ({ page }) => {
  const h = await openTrip(page, { failPost: "after" });
  await page.locator('#handoverButtons [data-type="picked_up"]').click();
  await expect(page.locator("#toast")).toHaveText("Synthetic error after commit");
  await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(0);
  h.children[0].pickup_status = "pending";
  await page.evaluate(() => refreshGroupContext());
  await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(0);
  expect(h.requests).toHaveLength(1);
});

test("iOS retains its existing successful driver handover flow", async ({ page }) => {
  const h = await openTrip(page, { platform: "ios" });
  await page.locator('#handoverButtons [data-type="picked_up"]').click();
  await expect(page.locator("#handoverButtons button:enabled")).toHaveCount(0);
  expect(h.requests.map((r) => r.action)).toEqual(["picked_up"]);
});

test("iOS retains its existing handover flow and map behaviour", async ({ page }) => {
  const h = await openTrip(page, { platform: "ios", parent: true });
  await expect(page.locator("#tripMap iframe")).toHaveCount(1);
  h.children[0].pickup_status = "picked_up";
  await page.evaluate(() => refreshGroupContext());
  await expect(page.locator("#tripMap iframe")).toHaveCount(1);
  expect(h.requests).toHaveLength(0);
});
