// UX-0.2 逐状态动作矩阵 + 键盘路径 + 200% zoom 验收
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");

const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8766";
const executablePath = process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE;
const outputRoot = path.resolve("var/acceptance/v0.3-ux0/states");
await fs.mkdir(outputRoot, { recursive: true });

// 动作矩阵：1 = 应可见，0 = 应隐藏（refresh 恒可见，不列入）
const MATRIX = [
  { id: "run-ux0-complete",       label: "已完成",   cancel: 0, rerun: 1, followUp: 1, retry: 0, fail: 0 },
  { id: "run-ux0-partial",        label: "部分完成", cancel: 0, rerun: 1, followUp: 1, retry: 0, fail: 0 },
  { id: "run-ux0-working",        label: "研究中",   cancel: 1, rerun: 0, followUp: 0, retry: 0, fail: 0 },
  { id: "run-ux0-pending",        label: "等待处理", cancel: 1, rerun: 0, followUp: 0, retry: 0, fail: 0 },
  { id: "run-ux0-needs-attention", label: "需要决定", cancel: 0, rerun: 0, followUp: 0, retry: 1, fail: 1 },
  { id: "run-ux0-cancelling",     label: "正在取消", cancel: 0, rerun: 0, followUp: 0, retry: 0, fail: 0 },
  { id: "run-ux0-failed",         label: "未完成",   cancel: 0, rerun: 1, followUp: 0, retry: 0, fail: 0 },
  { id: "run-ux0-cancelled",      label: "已取消",   cancel: 0, rerun: 0, followUp: 0, retry: 0, fail: 0 },
  { id: "run-ux0-expired",        label: "已过期",   cancel: 0, rerun: 1, followUp: 0, retry: 0, fail: 0 },
];

const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(e.message));

await page.goto(baseUrl, { waitUntil: "networkidle" });
await page.locator(".run-item").first().waitFor({ state: "visible" });

const matrixResults = [];
for (const row of MATRIX) {
  await page.locator(`.run-item[data-run-id="${row.id}"]`).click();
  await page.waitForFunction(
    (expected) => document.querySelector("#run-state")?.textContent?.trim() === expected,
    row.label, { timeout: 15_000 }
  );
  const vis = {
    cancel: await page.locator("#cancel-run").isVisible(),
    rerun: await page.locator("#rerun-run").isVisible(),
    followUp: await page.locator("#follow-up-run").isVisible(),
    retry: await page.locator("#retry-run").isVisible(),
    fail: await page.locator("#fail-run").isVisible(),
    refresh: await page.locator("#refresh-run").isVisible(),
  };
  assert.equal(vis.refresh, true, `${row.id}: refresh 应恒可见`);
  for (const key of ["cancel", "rerun", "followUp", "retry", "fail"]) {
    assert.equal(vis[key], Boolean(row[key]), `${row.id}: #${key} 可见性应为 ${row[key]}，实际 ${vis[key]}`);
  }
  matrixResults.push({ run: row.id, state: row.label, ...vis });
  await page.screenshot({ path: path.join(outputRoot, `state-${row.id.replace("run-ux0-", "")}.png`), fullPage: false });
}

// complete：Evidence inspector 双条翻页
await page.locator('.run-item[data-run-id="run-ux0-complete"]').click();
await page.waitForFunction(() => document.querySelectorAll(".evidence-item").length === 2, null, { timeout: 15_000 });
await page.locator(".evidence-item").nth(0).click();
assert.equal(await page.locator("#evidence-inspector").isVisible(), true);
assert.equal((await page.locator("#insp-position").innerText()).trim(), "1 / 2");
await page.locator("#insp-next").click();
assert.equal((await page.locator("#insp-position").innerText()).trim(), "2 / 2");
assert.equal(await page.locator("#insp-next").isDisabled(), true);
await page.keyboard.press("Escape");
assert.equal(await page.locator("#evidence-inspector").isVisible(), false);
assert.equal(
  await page.evaluate(() => document.activeElement === document.querySelectorAll(".evidence-item")[0]),
  true,
  "Inspector 关闭后焦点应返回触发证据"
);

// partial：boundary 三色语义
await page.locator('.run-item[data-run-id="run-ux0-partial"]').click();
await page.waitForFunction(() => document.querySelectorAll(".boundary-item").length >= 3, null, { timeout: 15_000 });
const kinds = await page.locator(".boundary-item").evaluateAll((els) => els.map((el) => el.dataset.kind));
assert.ok(kinds.includes("limitation") && kinds.includes("unmet") && kinds.includes("action"), `boundary 语义不全: ${kinds}`);
await page.screenshot({ path: path.join(outputRoot, "state-partial-boundaries.png"), fullPage: false });

// 键盘路径 1：新建 dialog 打开 → Escape 关闭 → 焦点返回触发按钮
await page.locator("#new-task").click();
assert.equal(await page.locator("#task-dialog").isVisible(), true);
await page.keyboard.press("Escape");
assert.equal(await page.locator("#task-dialog").isVisible(), false);
const focused = await page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName);
assert.equal(focused, "new-task", `Escape 关闭后焦点应返回 #new-task，实际 ${focused}`);

// 键盘路径 2：标准 tablist 方向键切换面板与 roving tabindex
await page.locator("#answer-tab").focus();
await page.keyboard.press("ArrowRight");
assert.equal(await page.locator("#activity-panel").isVisible(), true);
assert.equal(await page.locator("#answer-panel").isVisible(), false);
assert.equal(await page.locator("#activity-tab").getAttribute("tabindex"), "0");
assert.equal(await page.locator("#answer-tab").getAttribute("tabindex"), "-1");
await page.keyboard.press("ArrowLeft");
assert.equal(await page.locator("#answer-panel").isVisible(), true);

// 键盘路径 3：follow-up dialog Escape + 焦点返回
await page.locator('.run-item[data-run-id="run-ux0-complete"]').click();
await page.locator("#follow-up-run").click();
assert.equal(await page.locator("#follow-up-dialog").isVisible(), true);
await page.keyboard.press("Escape");
assert.equal(await page.locator("#follow-up-dialog").isVisible(), false);
const focused2 = await page.evaluate(() => document.activeElement?.id || "");
assert.equal(focused2, "follow-up-run", `焦点应返回 #follow-up-run，实际 ${focused2}`);

// 真实取消流程（working → 正在取消/已取消；无活动 lease 时 Runtime 直接落终态）
await page.locator('.run-item[data-run-id="run-ux0-working"]').click();
await page.locator("#cancel-run").click();
await page.waitForFunction(
  () => ["正在取消", "已取消"].includes(document.querySelector("#run-state")?.textContent?.trim() || ""),
  null, { timeout: 15_000 }
);
const afterCancel = (await page.locator("#run-state").innerText()).trim();
assert.ok(["正在取消", "已取消"].includes(afterCancel));
assert.equal(await page.locator("#cancel-run").isVisible(), false);
await page.screenshot({ path: path.join(outputRoot, "action-cancel-working.png"), fullPage: false });

// 200% zoom 等效（720 CSS px + dsf 2）：无横向溢出 + 核心操作可用
const zoomPage = await browser.newPage({ viewport: { width: 720, height: 450 }, deviceScaleFactor: 2 });
const zoomErrors = [];
zoomPage.on("console", (m) => { if (m.type() === "error") zoomErrors.push(m.text()); });
zoomPage.on("pageerror", (e) => zoomErrors.push(e.message));
await zoomPage.goto(baseUrl, { waitUntil: "networkidle" });
await zoomPage.locator(".run-item").first().waitFor({ state: "visible" });
await zoomPage.locator('.run-item[data-run-id="run-ux0-complete"]').click();
await zoomPage.waitForFunction(
  () => document.querySelector("#run-state")?.textContent?.trim() === "已完成",
  null, { timeout: 15_000 }
);
await zoomPage.waitForFunction(() => document.querySelectorAll(".evidence-item").length === 2, null, { timeout: 15_000 });
// <1024 → evidence 走 dialog
await zoomPage.locator(".evidence-item").nth(0).click();
assert.equal(await zoomPage.locator("#evidence-dialog").isVisible(), true);
await zoomPage.keyboard.press("Escape");
assert.equal(await zoomPage.locator("#evidence-dialog").isVisible(), false);
// 新建任务 dialog 在 200% zoom 下可打开可关闭
await zoomPage.locator("#new-task").click();
assert.equal(await zoomPage.locator("#task-dialog").isVisible(), true);
await zoomPage.keyboard.press("Escape");
const zoomOverflow = await zoomPage.evaluate(() => ({
  document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
}));
assert.equal(zoomOverflow.document, 0, `200% zoom 横向溢出 ${zoomOverflow.document}px`);
assert.deepEqual(zoomErrors, []);
await zoomPage.screenshot({ path: path.join(outputRoot, "zoom-200-complete.png"), fullPage: true });
await zoomPage.close();

assert.deepEqual(consoleErrors, []);

const summary = {
  schema_version: "conflux-weave.ux02-state-matrix.v1",
  base_url: baseUrl,
  states_verified: MATRIX.length,
  matrix: matrixResults,
  keyboard: ["escape-returns-focus-task-dialog", "arrow-keys-switch-tabs", "escape-returns-focus-follow-up", "escape-closes-inspector-and-returns-focus"],
  cancel_flow: `working -> ${afterCancel} verified`,
  zoom_200: { overflow: zoomOverflow.document, console_errors: zoomErrors },
  console_errors: consoleErrors,
};
await fs.writeFile(path.join(outputRoot, "summary.json"), JSON.stringify(summary, null, 2) + "\n", "utf8");
console.log(JSON.stringify(summary, null, 2));
await browser.close();
