import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");

const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8765";
const executablePath = process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE;
const outputRoot = path.resolve("var/acceptance/v0.3-ux0/final");
await fs.mkdir(outputRoot, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

await page.goto(baseUrl, { waitUntil: "networkidle" });
await page.locator("#run-state").waitFor({ state: "visible" });

const runPage = await (await fetch(`${baseUrl}/api/v1/runs?limit=100`)).json();
const runDetails = [];
for (const item of runPage.items) {
  runDetails.push(await (await fetch(`${baseUrl}/api/v1/runs/${encodeURIComponent(item.run_id)}`)).json());
}
const noAnswerRun = runDetails.find((run) =>
  run.state === "complete" && run.delivery?.disposition === "no_answer"
);
const evidenceRun = runDetails.find((run) =>
  run.task_family === "verified_paper_research"
  && run.state === "complete"
  && run.delivery?.disposition === "complete"
  && run.delivery.evidence_ids.length > 0
);
assert.ok(noAnswerRun, "persisted history must include a no-answer Run");
assert.ok(evidenceRun, "persisted history must include an evidence-backed single-Agent Run");

await page.locator(`.run-item[data-run-id="${noAnswerRun.run_id}"]`).click();
await page.waitForFunction(
  (runId) => document.querySelector(".run-item.selected")?.dataset.runId === runId,
  noAnswerRun.run_id
);
await page.waitForFunction(() => !document.querySelector("#limitations-section")?.hidden);
assert.equal(await page.locator(".evidence-item").count(), 0);
assert.equal(await page.locator("#evidence-section").isVisible(), false);

await page.locator(`.run-item[data-run-id="${evidenceRun.run_id}"]`).click();
await page.waitForFunction(
  (runId) => document.querySelector(".run-item.selected")?.dataset.runId === runId,
  evidenceRun.run_id,
  { timeout: 60_000 }
);
const terminalState = (await page.locator("#run-state").innerText()).trim();
assert.equal(terminalState, "已完成");
await page.waitForFunction(
  (count) => document.querySelectorAll(".evidence-item").length === count,
  evidenceRun.delivery.evidence_ids.length,
  { timeout: 30_000 },
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
  path: path.join(outputRoot, "desktop-1440-complete.png"),
  fullPage: true,
});

// UX-0.1: desktop >=1024 打开右侧 Inspector 而非 dialog
const evidenceButtons = page.locator(".evidence-item");
const evidenceCount = await evidenceButtons.count();
assert.ok(evidenceCount > 0);
await evidenceButtons.nth(0).click();
assert.equal(await page.locator("#evidence-inspector").isVisible(), true);
assert.equal(await page.locator("#evidence-dialog").isVisible(), false);
assert.ok((await page.locator("#insp-source").innerText()).trim().length > 0);
assert.equal((await page.locator("#insp-position").innerText()).trim(), `1 / ${evidenceCount}`);
await page.screenshot({
  path: path.join(outputRoot, "desktop-1440-inspector.png"),
  fullPage: false,
});
if (evidenceCount > 1) {
  await page.locator("#insp-next").click();
  assert.equal((await page.locator("#insp-position").innerText()).trim(), `2 / ${evidenceCount}`);
  await page.locator("#insp-prev").click();
}
await page.locator("#close-inspector").click();
assert.equal(await page.locator("#evidence-inspector").isVisible(), false);

// UX-0.1: HUD 折叠/展开
await page.locator("#hud-toggle").click();
assert.equal(await page.locator("#hud-body").isVisible(), true);
assert.ok((await page.locator("#hud-provider").innerText()).trim().length > 0);
await page.screenshot({
  path: path.join(outputRoot, "desktop-1440-hud.png"),
  fullPage: false,
});
await page.locator("#hud-toggle").click();
assert.equal(await page.locator("#hud-body").isVisible(), false);

// UX-0.1: 侧栏窄档
await page.locator("#toggle-sidebar").click();
assert.equal(await page.locator("#run-list").isVisible(), false);
await page.screenshot({
  path: path.join(outputRoot, "desktop-1440-sidebar-narrow.png"),
  fullPage: false,
});
await page.locator("#toggle-sidebar").click();
assert.equal(await page.locator("#run-list").isVisible(), true);

// follow-up dialog 流程（未改动，回归确认）
await page.locator("#follow-up-run").click();
assert.equal(await page.locator("#follow-up-dialog").isVisible(), true);
await page.locator("#follow-up-question").fill(
  "Which evaluation most directly measures whether context reduction preserves tool success?"
);
await page.locator("#follow-up-dialog .dialog-actions button[value='cancel']").click();

const viewports = [
  { name: "desktop-1024", width: 1024, height: 768 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "mobile-390", width: 390, height: 844 },
  { name: "mobile-320", width: 320, height: 568 },
];
const overflow = {};
for (const viewport of viewports) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.waitForTimeout(250);
  await page.screenshot({
    path: path.join(outputRoot, `${viewport.name}-complete.png`),
    fullPage: true,
  });
  overflow[viewport.name] = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    runView: Math.max(0, document.querySelector("#run-view").scrollWidth - document.querySelector("#run-view").clientWidth),
  }));
  assert.deepEqual(overflow[viewport.name], { document: 0, runView: 0 }, `overflow at ${viewport.name}`);
}

// <1024：Evidence 走 dialog 模式
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(250);
await evidenceButtons.nth(0).click();
assert.equal(await page.locator("#evidence-dialog").isVisible(), true);
assert.equal(await page.locator("#evidence-inspector").isVisible(), false);
await page.screenshot({
  path: path.join(outputRoot, "mobile-390-evidence-dialog.png"),
  fullPage: false,
});
await page.locator("[data-close-evidence]").click();

assert.deepEqual(consoleErrors, []);

const summary = {
  schema_version: "conflux-weave.ux0-browser-verification.v1",
  base_url: baseUrl,
  run_source: "persisted_history",
  selected_run_id: evidenceRun.run_id,
  no_answer_run_id: noAnswerRun.run_id,
  no_answer_boundary_verified: true,
  terminal_state: terminalState,
  evidence_count: evidenceCount,
  viewports: ["1440x900", "1024x768", "768x1024", "390x844", "320x568"],
  overflow,
  console_errors: consoleErrors,
  screenshots: [
    "desktop-1440-complete.png",
    "desktop-1440-inspector.png",
    "desktop-1440-hud.png",
    "desktop-1440-sidebar-narrow.png",
    "desktop-1024-complete.png",
    "tablet-768-complete.png",
    "mobile-390-complete.png",
    "mobile-320-complete.png",
    "mobile-390-evidence-dialog.png",
  ],
};
await fs.writeFile(
  path.join(outputRoot, "summary.json"),
  JSON.stringify(summary, null, 2) + "\n",
  "utf8"
);
console.log(JSON.stringify(summary, null, 2));
await browser.close();
