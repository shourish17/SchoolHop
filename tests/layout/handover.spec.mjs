import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

// Both native shells use the same existing trip/child response shape.
async function openTrip(page, platform, tripType, status = "pending") {
  const requests = [];
  const child = { child_id: "synthetic-child", name: "Test Child", pickup_status: "pending", dropoff_status: "pending" };
  const statusColumn = tripType === "pickup" ? "pickup_status" : "dropoff_status";
  child[statusColumn] = status;
  if (tripType === "dropoff" && status === "absent") child.pickup_status = "absent";
  const trip = { id: "synthetic-trip", group_id: "synthetic-group", driver_user_id: "synthetic-driver", created_by: "synthetic-driver",
    driver: { id: "synthetic-driver", name: "Driver" }, trip_type: tripType, service_date: new Date().toISOString().slice(0, 10),
    expected_time: "08:00:00", status: "planned", my_response: { status: "accepted" }, children: [child], latest_location: null };
  await page.addInitScript(({ platform }) => {
    if (window.top !== window) return;
    localStorage.setItem("schoolhop_token", "synthetic-account");
    sessionStorage.setItem("schoolhop_selected_trip", "synthetic-trip");
    window.Capacitor = { isNativePlatform: () => true, getPlatform: () => platform, Plugins: {} };
  }, { platform });
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/")) {
      let json = [];
      if (path === "/api/config") json = {};
      if (path === "/api/me") json = { user: { id: "synthetic-driver", email: "driver@example.test" } };
      if (path === "/api/groups") json = [{ id: "synthetic-group", name: "Test Group" }];
      if (path === "/api/groups/synthetic-group") json = { group: { id: "synthetic-group", created_by: "synthetic-driver" }, members: [], invitations: [], approvals: [] };
      if (path === "/api/groups/synthetic-group/trips") json = [trip];
      if (path === "/api/trips/synthetic-trip") json = trip;
      if (path.endsWith("/handover")) {
        const action = route.request().postDataJSON().handover_type;
        requests.push(action);
        child[statusColumn] = action;
        json = { id: "synthetic-event", handover_type: action };
      }
      return route.fulfill({ json });
    }
    const files = { "/": "index.html", "/static/app.js": "app.js", "/static/styles.css": "styles.css" };
    if (!files[path]) return route.abort();
    return route.fulfill({ body: await readFile(new URL(`../../app/static/${files[path]}`, import.meta.url)),
      contentType: path.endsWith(".js") ? "text/javascript" : path.endsWith(".css") ? "text/css" : "text/html" });
  });
  await page.goto("https://schoolhop.test/?tab=trip&trip=synthetic-trip");
  return requests;
}

for (const platform of ["ios", "android"]) {
  for (const [type, action, other, label] of [["pickup", "picked_up", "dropped_off", "Picked Up"], ["dropoff", "dropped_off", "picked_up", "Dropped Off"]]) {
    test(`${platform} ${type} exposes only ${label} and completes once`, async ({ page }) => {
      const requests = await openTrip(page, platform, type);
      const button = page.locator(`#handoverButtons [data-type="${action}"]`);
      await expect(button).toHaveText(label);
      await expect(button).toBeEnabled();
      await expect(page.locator(`#handoverButtons [data-type="${other}"]`)).toHaveCount(0);
      await button.click();
      await expect(button).toBeDisabled();
      await expect(page.locator('#handoverButtons [data-type="absent"]')).toBeDisabled();
      expect(requests).toEqual([action]);
    });
  }
  test(`${platform} legacy dropoff absence disables all further handovers`, async ({ page }) => {
    const requests = await openTrip(page, platform, "dropoff", "absent");
    await expect(page.locator('#handoverButtons [data-type="dropped_off"]')).toBeDisabled();
    await expect(page.locator('#handoverButtons [data-type="absent"]')).toBeDisabled();
    expect(requests).toEqual([]);
  });
}
