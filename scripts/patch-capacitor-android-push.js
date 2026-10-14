"use strict";

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const pluginsPath = path.join(root, "android", "app", "src", "main", "assets", "capacitor.plugins.json");
const stockClassPath = "com.capacitorjs.plugins.pushnotifications.PushNotificationsPlugin";
const guardedClassPath = "com.schoolhop.app.SchoolHopPushNotificationsPlugin";

const plugins = JSON.parse(fs.readFileSync(pluginsPath, "utf8"));
let patched = false;

for (const plugin of plugins) {
  if (plugin.pkg === "@capacitor/push-notifications" && plugin.classpath === stockClassPath) {
    plugin.classpath = guardedClassPath;
    patched = true;
  }
}

if (!patched && !plugins.some((plugin) => plugin.pkg === "@capacitor/push-notifications" && plugin.classpath === guardedClassPath)) {
  throw new Error("Could not find Capacitor Push Notifications plugin entry to patch");
}

fs.writeFileSync(pluginsPath, `${JSON.stringify(plugins, null, "\t")}\n`);
console.log("Patched Android PushNotifications plugin to use SchoolHop Firebase guard");
