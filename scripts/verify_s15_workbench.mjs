import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8765";
const executablePath = process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE;
const submitLive = process.env.CONFLUX_WEAVE_SUBMIT_LIVE === "1";
const outputRoot = path.resolve("var/acceptance/v0.3-s1/browser-s15b");
await fs.mkdir(outputRoot, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

await page.goto(baseUrl, { waitUntil: "networkidle" });
if (submitLive) {
  await page.locator("#new-task").click();
  await page.getByLabel("Manager").check();
  assert.equal(await page.locator("#managed-options").isVisible(), true);
  await page.getByLabel("单 Agent").check();
  await page.locator("#query").fill(
    "What mechanisms reduce context usage in long-horizon tool-using LLM agents?"
  );
  await page.locator("#submit-task").click();
}

await page.locator("#run-state").waitFor({ state: "visible" });
await page.waitForFunction(
  () => ["已完成", "部分完成", "未完成"].includes(
    document.querySelector("#run-state")?.textContent?.trim() || ""
  ),
  { timeout: 180_000 }
);
const terminalState = (await page.locator("#run-state").innerText()).trim();
assert.equal(terminalState, "已完成");
await page.waitForFunction(
  () => document.querySelectorAll(".evidence-item").length > 0,
  { timeout: 30_000 }
);
assert.equal((await page.locator("#run-mode").innerText()).trim(), "单 Agent");
assert.match(await page.locator("#corpus-scope").innerText(), /LanceDB/);
assert.equal(
  (await page.locator("#confidence-value").innerText()).trim(),
  "引用核验完成"
);
assert.equal(await page.locator("#rerun-run").isVisible(), true);
assert.equal(await page.locator("#follow-up-run").isVisible(), true);

await page.screenshot({
  path: path.join(outputRoot, "desktop-complete.png"),
  fullPage: true,
});

const evidenceButtons = page.locator(".evidence-item");
const evidenceCount = await evidenceButtons.count();
assert.ok(evidenceCount > 0);
await evidenceButtons.nth(0).click();
assert.equal(await page.locator("#evidence-dialog").isVisible(), true);
assert.ok((await page.locator("#evidence-source").innerText()).trim().length > 0);
await page.screenshot({
  path: path.join(outputRoot, "desktop-evidence.png"),
  fullPage: false,
});
await page.locator("[data-close-evidence]").click();

await page.locator("#follow-up-run").click();
assert.equal(await page.locator("#follow-up-dialog").isVisible(), true);
await page.locator("#follow-up-question").fill(
  "Which evaluation most directly measures whether context reduction preserves tool success?"
);
await page.screenshot({
  path: path.join(outputRoot, "desktop-follow-up.png"),
  fullPage: false,
});
await page.locator("#follow-up-dialog .dialog-actions button[value='cancel']").click();

await page.setViewportSize({ width: 390, height: 844 });
await page.screenshot({
  path: path.join(outputRoot, "mobile-complete.png"),
  fullPage: true,
});
const overflow = await page.evaluate(() => ({
  document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  runView: Math.max(0, document.querySelector("#run-view").scrollWidth - document.querySelector("#run-view").clientWidth),
}));
assert.deepEqual(overflow, { document: 0, runView: 0 });
assert.deepEqual(consoleErrors, []);

const summary = {
  schema_version: "conflux-weave.s15b-browser-verification.v1",
  base_url: baseUrl,
  run_source: submitLive ? "submitted_live" : "persisted_history",
  terminal_state: terminalState,
  evidence_count: evidenceCount,
  viewports: ["1440x900", "390x844"],
  overflow,
  console_errors: consoleErrors,
  screenshots: [
    "desktop-complete.png",
    "desktop-evidence.png",
    "desktop-follow-up.png",
    "mobile-complete.png",
  ],
};
await fs.writeFile(
  path.join(outputRoot, "summary.json"),
  JSON.stringify(summary, null, 2) + "\n",
  "utf8"
);
console.log(JSON.stringify(summary, null, 2));
await browser.close();
