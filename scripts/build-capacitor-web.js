"use strict";

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const source = path.join(root, "app", "static");
const destination = path.join(root, "build", "capacitor");
const productionUrl = (process.env.SCHOOLHOP_IOS_API_BASE_URL || "https://schoolhop.shourish.com").replace(/\/$/, "");

function copyDirectory(from, to) {
  fs.rmSync(to, { recursive: true, force: true });
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    const sourcePath = path.join(from, entry.name);
    const destinationPath = path.join(to, entry.name);
    if (entry.isDirectory()) {
      copyDirectory(sourcePath, destinationPath);
    } else {
      fs.copyFileSync(sourcePath, destinationPath);
    }
  }
}

fs.rmSync(destination, { recursive: true, force: true });
fs.mkdirSync(destination, { recursive: true });
copyDirectory(source, path.join(destination, "static"));
fs.copyFileSync(path.join(source, "index.html"), path.join(destination, "index.html"));

const indexPath = path.join(destination, "index.html");
let html = fs.readFileSync(indexPath, "utf8");
const nativeConfig = [
  "<script>",
  "window.SCHOOLHOP_NATIVE_CONFIG = {",
  `  apiBaseUrl: ${JSON.stringify(productionUrl)},`,
  `  appVersion: ${JSON.stringify(process.env.npm_package_version || "0.1.0")}`,
  "};",
  "</script>",
].join("\n");
html = html.replace("</head>", `${nativeConfig}\n</head>`);
fs.writeFileSync(indexPath, html);

console.log(`Prepared SchoolHop Capacitor assets in ${path.relative(root, destination)} using API ${productionUrl}`);
