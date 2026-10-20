import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(new URL("../../app/static/app.js", import.meta.url), "utf8");
const context = vm.createContext({ escapeHTML: (s) => String(s) });
vm.runInContext(source.slice(source.indexOf("function handoverButtonsHTML("), source.indexOf("function renderSwapRequestPanel(")), context);

for (const [type, action, other, label] of [["pickup", "picked_up", "dropped_off", "Picked Up"], ["dropoff", "dropped_off", "picked_up", "Dropped Off"]]) {
  test(`${type} shows only ${label} completion and retains absence`, () => {
    const trip = { trip_type: type, children: [{ child_id: "synthetic-child", name: "Test", pickup_status: "pending", dropoff_status: "pending" }] };
    const html = context.handoverButtonsHTML(trip, true);
    assert.ok(html.includes(`data-type="${action}"`));
    assert.ok(!html.includes(`data-type="${other}"`));
    assert.ok(html.includes(`>${label}</button>`));
    assert.ok(html.includes('data-type="absent"'));
    for (const status of [action, "absent"]) {
      trip.children[0][type === "pickup" ? "pickup_status" : "dropoff_status"] = status;
      const completed = context.handoverButtonsHTML(trip, true);
      assert.match(completed, new RegExp(`data-type="${action}" disabled`));
      assert.match(completed, /data-type="absent" disabled/);
    }
    assert.match(context.handoverButtonsHTML(trip, false), new RegExp(`data-type="${action}" disabled`));
  });
}
