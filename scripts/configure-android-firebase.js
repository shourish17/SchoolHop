"use strict";

const fs = require("fs");
const path = require("path");

// Never log parse errors: they can contain fragments of the configuration.
function validateFirebaseConfig(contents) {
  let config;
  try { config = JSON.parse(contents); } catch { throw new Error("Invalid Firebase Android configuration JSON"); }
  if (config.project_info?.project_id !== "schoolhop-1c00d" ||
      !config.project_info?.project_number ||
      !config.client?.some((client) =>
        client.client_info?.android_client_info?.package_name === "com.schoolhop.app" &&
        client.client_info?.mobilesdk_app_id && client.api_key?.some((key) => key.current_key))) {
    throw new Error("Firebase configuration must match schoolhop-1c00d and com.schoolhop.app");
  }
}

function configure() {
  const target = path.resolve(__dirname, "../android/app/google-services.json");
  const source = process.env.ANDROID_GOOGLE_SERVICES_FILE;
  const encoded = process.env.ANDROID_GOOGLE_SERVICES_JSON_BASE64;
  if (source && encoded) throw new Error("Specify only one Firebase configuration source");
  let contents;
  if (encoded) {
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) throw new Error("Invalid Firebase configuration encoding");
    contents = Buffer.from(encoded, "base64").toString("utf8");
  } else if (source) {
    try { contents = fs.readFileSync(source, "utf8"); } catch { throw new Error("Unable to read external Firebase configuration file"); }
  } else if (fs.existsSync(target)) {
    contents = fs.readFileSync(target, "utf8");
  } else {
    if (process.env.ANDROID_REQUIRE_FIREBASE === "true") throw new Error("Missing ANDROID_GOOGLE_SERVICES_JSON_BASE64 secret or ANDROID_GOOGLE_SERVICES_FILE");
    console.log("Firebase Android configuration unavailable; building with push disabled");
    return;
  }
  validateFirebaseConfig(contents);
  fs.writeFileSync(target, contents, { mode: 0o600 });
  fs.chmodSync(target, 0o600);
  console.log("Firebase Android configuration validated and staged (contents hidden)");
}

if (require.main === module) {
  try { configure(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}

module.exports = { validateFirebaseConfig };
