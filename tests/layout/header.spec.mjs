import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

// Serve the real frontend with isolated API fixtures; never contact a live backend.
async function openApp(page, { native = true } = {}) {
  await page.addInitScript(({ native }) => {
    if (!localStorage.getItem("layout_fixture_initialized")) {
      localStorage.setItem("layout_fixture_initialized", "true");
      localStorage.setItem("schoolhop_token", "layout-test-token");
      sessionStorage.setItem("schoolhop_selected_trip", "old-trip");
      sessionStorage.setItem("schoolhop_selected_roster_driver", "old-driver");
    }
    if (native) {
      window.Capacitor = { isNativePlatform: () => true, getPlatform: () => "android", Plugins: {} };
    }
  }, { native });
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/")) {
      const json = path === "/api/me"
        ? { user: { id: "parent", email: "a-very-long-signed-in-parent-name@example.test" } }
        : path === "/api/config" ? {} : [];
      return route.fulfill({ json });
    }
    const files = { "/": "index.html", "/static/app.js": "app.js", "/static/styles.css": "styles.css" };
    if (!files[path]) return route.abort();
    const body = await readFile(new URL(`../../app/static/${files[path]}`, import.meta.url));
    const contentType = path.endsWith(".js") ? "text/javascript" : path.endsWith(".css") ? "text/css" : "text/html";
    return route.fulfill({ body, contentType });
  });
  await page.goto("https://schoolhop.test/");
  await expect(page.locator("#sessionSummary")).toContainText("example.test");
}

async function expectUsableHeader(page) {
  const logout = page.locator("#logout");
  await expect(logout).toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    const header = document.querySelector(".topbar").getBoundingClientRect();
    const tabs = document.querySelector(".tabs").getBoundingClientRect();
    return tabs.top >= header.bottom - 1;
  })).toBe(true);
  const geometry = await page.evaluate(() => {
    const button = document.querySelector("#logout");
    const box = button.getBoundingClientRect();
    const brand = document.querySelector(".brand").getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return {
      height: box.height, left: box.left, right: box.right, top: box.top,
      width: innerWidth, brandRight: brand.right,
      hit: hit === button || button.contains(hit),
      overflow: document.documentElement.scrollWidth > innerWidth,
      paddingTop: getComputedStyle(document.querySelector(".topbar")).paddingTop,
    };
  });
  expect(geometry.height).toBeGreaterThanOrEqual(48);
  expect(geometry.top).toBeGreaterThanOrEqual(0);
  expect(geometry.left).toBeGreaterThanOrEqual(geometry.brandRight);
  expect(geometry.right).toBeLessThanOrEqual(geometry.width);
  expect(geometry.hit).toBe(true);
  expect(geometry.overflow).toBe(false);
  // Android's native container already removes the unsafe area from this viewport.
  expect(geometry.paddingTop).toBe("12px");
}

for (const [name, width, height, scale] of [
  ["small phone", 320, 568, 1],
  ["Pixel 7 portrait", 412, 820, 1],
  ["Pixel 7 landscape", 915, 380, 1],
  ["large display and text", 320, 568, 2],
  ["tablet", 800, 1280, 1],
  ["desktop PWA", 1440, 900, 1],
]) {
  test(`${name}: header, tabs and Sign Out remain usable`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openApp(page, { native: name !== "desktop PWA" });
    await page.evaluate((scale) => { document.documentElement.style.fontSize = `${16 * scale}px`; }, scale);
    await expectUsableHeader(page);
    // Exercise sticky positions after the content moves underneath the header.
    await page.evaluate(() => {
      document.querySelector("#home").style.minHeight = "200vh";
      window.scrollTo(0, 300);
    });
    await expectUsableHeader(page);
    await page.locator("#logout").tap();
    await expect(page.locator("#loginForm")).toBeVisible();
    await expect(page.locator("#app")).toBeHidden();
    await expect(page.locator("#logout")).toBeHidden();
    expect(await page.evaluate(() => localStorage.getItem("schoolhop_token"))).toBeNull();
    expect(await page.evaluate(() => sessionStorage.getItem("schoolhop_selected_trip"))).toBeNull();
    expect(await page.evaluate(() => sessionStorage.getItem("schoolhop_selected_roster_driver"))).toBeNull();
    await page.reload();
    await expect(page.locator("#loginForm")).toBeVisible();
    await expect(page.locator("#app")).toBeHidden();
  });
}

test("header follows rotation and install control changes; offline Sign Out returns to login", async ({ page, context }) => {
  await page.setViewportSize({ width: 412, height: 820 });
  await openApp(page);
  await page.evaluate(() => document.querySelector("#installApp").classList.remove("hidden"));
  await expectUsableHeader(page);
  await page.setViewportSize({ width: 820, height: 360 });
  await expectUsableHeader(page);
  await page.setViewportSize({ width: 320, height: 568 });
  await expectUsableHeader(page);
  await page.locator("#showRegister").evaluate((button) => button.click());
  await page.unrouteAll({ behavior: "wait" });
  await context.setOffline(true);
  await page.locator("#logout").click();
  await expect(page.locator("#loginForm")).toBeVisible();
  await expect(page.locator("#registerStartForm")).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem("schoolhop_token"))).toBeNull();
});
