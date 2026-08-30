import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");

const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8767";
const executablePath = process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE;
const outputRoot = path.resolve("var/acceptance/v0.3-ux1/final");
await fs.mkdir(outputRoot, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

const sections = ["overview", "chat", "research", "settings"];

async function gotoSection(name) {
  await page.locator(`[data-section-link="${name}"]`).click();
  await page.waitForFunction(
    (expected) => document.querySelector(".app-shell")?.dataset.section === expected,
    name,
  );
}

async function assertNoOverflow(label) {
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  assert.equal(overflow.document, 0, `document overflow at ${label}`);
}

// ---- 总览：默认落地页 ----
await page.goto(`${baseUrl}/#/overview`, { waitUntil: "networkidle" });
await page.locator("#overview-view").waitFor({ state: "visible" });
assert.equal(await page.locator(".run-sidebar").isVisible(), false, "non-research sections hide the sidebar");
await page.locator("#overview-checks .check-row").first().waitFor({ state: "visible" });
const checkCount = await page.locator("#overview-checks .check-row").count();
assert.equal(checkCount, 3, "three real readiness checks");
await page.locator("#overview-alert").waitFor({ state: "visible" });
assert.match(await page.locator("#overview-alert-text").innerText(), /模型服务/);
const overviewOverflow = await page.evaluate(() => ({
  document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
}));
assert.equal(overviewOverflow.document, 0);
await page.screenshot({ path: path.join(outputRoot, "desktop-1440-overview.png"), fullPage: true });

// ---- 设置：Provider 未生效引导 + 表单回显 + 保存 ----
await gotoSection("settings");
await page.locator("#provider-form").waitFor({ state: "visible" });
assert.equal(await page.locator(".run-sidebar").isVisible(), false);
await page.waitForFunction(() => document.querySelector("#cfg-base-url")?.value.length > 0);
assert.equal((await page.locator("#cfg-base-url").inputValue()), "https://127.0.0.1:1/v1");
assert.match(await page.locator("#cfg-api-key").getAttribute("placeholder"), /已配置/);
await page.locator("#cfg-model").fill("qwen-ux1-updated");
await page.locator("#save-provider").click();
await page.locator("#settings-banner.ok").waitFor({ state: "visible" });
assert.match(await page.locator("#settings-banner").innerText(), /重启/);
assert.equal(await page.locator("#cfg-api-key").inputValue(), "", "api key field cleared after save");
await page.waitForFunction(() => document.querySelector("#cfg-model")?.value === "qwen-ux1-updated");
await page.locator("#settings-checks .check-row").first().waitFor({ state: "visible" });
await page.screenshot({ path: path.join(outputRoot, "desktop-1440-settings.png"), fullPage: true });

// ---- 对话：UX-1.1 只读线程历史（根 Run → 追问链；父 Run 缺失时标记截断） ----
await gotoSection("chat");
await page.locator("#chat-view").waitFor({ state: "visible" });
// W3.0 三模式：深度研究走既有 verified-research 流程，需经向上弹出列表选择。
await page.locator("#chat-mode-button").click();
await page.locator('#chat-mode-list li[data-mode="deep"]').click();
assert.equal((await page.locator("#chat-mode-label").innerText()).trim(), "深度研究");
await page.locator("#chat-history").waitFor({ state: "visible" });
await page.locator(".chat-thread-item").first().waitFor({ state: "visible" });
assert.equal(await page.locator(".chat-thread-item").count(), 2, "two seeded history threads");
const newestThread = page.locator(".chat-thread-item").first();
assert.equal(await newestThread.getAttribute("open"), "", "most recent thread is expanded by default");
assert.match(await newestThread.locator(".chat-thread-title").innerText(), /记忆架构/);
assert.equal(await newestThread.locator(".chat-thread-body .chat-msg").count(), 4, "root and follow-up message pairs");
const olderThread = page.locator(".chat-thread-item").nth(1);
assert.equal(await olderThread.getAttribute("open"), null, "older thread collapsed by default");
assert.match(await olderThread.locator(".chat-thread-truncated").innerText(), /线程历史不完整/);
assert.match(await page.locator(".chat-history-link").getAttribute("href"), /#\/research/);

// ---- 对话：fixture 全流程（提交 → SSE → 交付 → 深链研究） ----
const chatQuestion = "UX-1 验证：离线 Harness fixture 能否覆盖 Context Bundle 与交付闭环？";
await page.locator("#chat-input").fill(chatQuestion);
await page.locator("#chat-send").click();
await page.locator("#chat-thread .chat-msg.user").waitFor({ state: "visible" });
await page.locator("#chat-thread .chat-msg.agent").first().waitFor({ state: "visible" });
// 终态：交付渲染（fixture 为 partial，answer 文本来自真实交付工件）
await page.waitForFunction(
  () => {
    const foot = document.querySelector("#chat-thread .chat-msg.agent .chat-msg-foot");
    return foot && !foot.hidden;
  },
  undefined,
  { timeout: 30_000 },
);
await page.waitForFunction(
  () => (document.querySelector("#chat-thread .chat-msg.agent .chat-msg-body")?.textContent || "").trim().length > 0,
  undefined,
  { timeout: 30_000 },
);
const agentBody = await page.locator("#chat-thread .chat-msg.agent .chat-msg-body").first().innerText();
assert.match(agentBody, /Harness/);
const agentMeta = await page.locator("#chat-thread .chat-msg-meta").first().innerText();
assert.match(agentMeta, /限制/);
await page.screenshot({ path: path.join(outputRoot, "desktop-1440-chat.png"), fullPage: true });

// 对话深链到研究视图（当前会话的 Run）
await page.locator("#chat-thread .chat-msg-actions .quiet-button").first().click();
await page.waitForFunction(
  () => window.location.hash.startsWith("#/research/"),
  undefined,
  { timeout: 10_000 },
);
const deepLinkedRunId = decodeURIComponent(
  (await page.evaluate(() => window.location.hash)).split("/")[2],
);
await page.locator("#run-view").waitFor({ state: "visible" });
await page.waitForFunction(
  (runId) => document.querySelector("#run-query")?.textContent?.length > 0,
  deepLinkedRunId,
);
assert.match(await page.locator("#run-query").innerText(), /UX-1 验证/);
assert.equal((await page.locator("#run-state").innerText()).trim(), "部分完成");
await page.screenshot({ path: path.join(outputRoot, "desktop-1440-research.png"), fullPage: true });

// 刷新恢复：hash 深链在 reload 后仍选中同一 Run
await page.reload({ waitUntil: "networkidle" });
await page.locator("#run-view").waitFor({ state: "visible" });
await page.waitForFunction(
  (runId) => document.querySelector("#run-query")?.textContent?.length > 0,
  deepLinkedRunId,
);
assert.match(await page.locator("#run-query").innerText(), /UX-1 验证/);

// ---- 总览最近研究：出现 fixture Run ----
await gotoSection("overview");
await page.locator("#overview-runs .overview-run").first().waitFor({ state: "visible" });
assert.match(await page.locator("#overview-runs .overview-run strong").first().innerText(), /UX-1 验证/);

// ---- 200% 缩放 ----
await page.setViewportSize({ width: 1280, height: 720 });
await page.evaluate(() => { document.body.style.zoom = "2"; });
await page.waitForTimeout(250);
await assertNoOverflow("overview at 200% zoom");
await page.evaluate(() => { document.body.style.zoom = ""; });

// ---- 五 viewport × 四分区零横向溢出 ----
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
  overflow[viewport.name] = {};
  for (const section of sections) {
    await gotoSection(section);
    await page.waitForTimeout(150);
    const measured = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    overflow[viewport.name][section] = measured.document;
    assert.equal(measured.document, 0, `overflow at ${viewport.name} ${section}`);
  }
  await page.screenshot({
    path: path.join(outputRoot, `${viewport.name}-overview.png`),
    fullPage: true,
  });
}

await page.setViewportSize({ width: 390, height: 844 });
await gotoSection("chat");
await page.screenshot({ path: path.join(outputRoot, "mobile-390-chat.png"), fullPage: true });

// ---- UX-1.1 新对话：仅清空当前前端视图，不删除历史线程 ----
await page.locator("#chat-new-thread").click();
await page.locator("#chat-thread-empty").waitFor({ state: "visible" });
assert.equal(await page.locator("#chat-thread .chat-msg").count(), 0, "new-thread clears the live view");
assert.equal(await page.locator(".chat-thread-item").count(), 2, "history threads are preserved");

assert.deepEqual(consoleErrors, [], "zero console errors");

const summary = {
  schema_version: "conflux-weave.ux1-browser-verification.v1",
  base_url: baseUrl,
  run_source: "offline_fixture_runtime",
  deep_linked_run_id: deepLinkedRunId,
  provider_not_ready_alert_verified: true,
  settings_save_restart_banner_verified: true,
  chat_fixture_flow_verified: true,
  chat_thread_view_verified: true,
  new_thread_view_only_verified: true,
  refresh_restore_verified: true,
  viewports: ["1440x900", "1024x768", "768x1024", "390x844", "320x568"],
  sections: sections,
  overflow,
  console_errors: consoleErrors,
  screenshots: [
    "desktop-1440-overview.png",
    "desktop-1440-settings.png",
    "desktop-1440-chat.png",
    "desktop-1440-research.png",
    "desktop-1024-overview.png",
    "tablet-768-overview.png",
    "mobile-390-overview.png",
    "mobile-320-overview.png",
    "mobile-390-chat.png",
  ],
};
await fs.writeFile(
  path.join(outputRoot, "summary.json"),
  JSON.stringify(summary, null, 2) + "\n",
  "utf8",
);
console.log(JSON.stringify(summary, null, 2));
await browser.close();
