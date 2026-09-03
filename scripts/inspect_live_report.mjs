import assert from "node:assert/strict";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");
const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8000";
const browser = await chromium.launch({ headless: true, executablePath: process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(`${baseUrl}/#/research`, { waitUntil: "networkidle" });
await page.locator("#answer-content").waitFor({ state: "attached" });
const runs = await page.evaluate(async () => (await fetch("/api/v1/runs?limit=20")).json());
const details = [];
for (const item of runs.items || []) {
  details.push(await (await fetch(`${baseUrl}/api/v1/runs/${encodeURIComponent(item.run_id)}`)).json());
}
const run = details.find((item) => item.delivery?.artifact_ids?.length) || details[0];
assert.ok(run, "no persisted Run available");
await page.locator(`.run-item[data-run-id="${run.run_id}"]`).click();
await page.waitForTimeout(800);
const report = await page.evaluate(() => ({
  hash: window.location.hash,
  tableCount: document.querySelectorAll("#answer-content .answer-table").length,
  tableRows: [...document.querySelectorAll("#answer-content .answer-table tbody tr")].length,
  rawPipeLines: [...document.querySelectorAll("#answer-content p")].filter((node) => /\|---|\|阶段\||\|层级\|/.test(node.textContent)).map((node) => node.textContent),
  bodyText: document.querySelector("#answer-content .answer-body")?.innerText || "",
  artifactText: document.querySelector("#answer-content")?.innerText || "",
}));
console.log(JSON.stringify({ run_id: run.run_id, query: run.query, report }, null, 2));
await page.screenshot({ path: "tmp/live-report-inspection.png", fullPage: true });
await browser.close();
