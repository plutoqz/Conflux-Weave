import assert from "node:assert/strict";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");
const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8000";

const fixture = `# 报告标题

## 问题与来源说明

总体结论：报告内容支持三个关键判断 [1][2][3]

## 二、核心机制

|层级|名称|说明| |---|---|---| |L0|模型|让系统能够启动| |L1|工具循环|让动作可以执行|。表格后的正文继续说明该结论。

## 来源引用

[1](https://example.com/one) 来源一[web] [2](https://example.com/two) 来源二[web] [3]《本地来源》[本地], 第3页
`;

const browser = await chromium.launch({ headless: true, executablePath: process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });

await page.goto(`${baseUrl}/#/research`, { waitUntil: "networkidle" });
await page.locator("#answer-content").waitFor({ state: "attached" });
await page.evaluate(async (value) => {
  const { renderAnswer } = await import("/assets/modules/shared.js?v=v0.3-report-render-4");
  renderAnswer(document.querySelector("#answer-content"), value, "text/markdown");
}, fixture);

assert.equal(await page.locator("#answer-content .answer-table").count(), 1);
assert.equal(await page.locator("#answer-content .answer-table tbody tr").count(), 2);
assert.equal(await page.locator("#answer-content").getByText("| --- | --- | --- |", { exact: false }).count(), 0);
assert.equal(await page.locator("#report-toc-rail").isVisible(), true);
assert.equal(await page.locator("#report-toc-rail .report-toc li").count(), 4);
assert.equal(await page.locator("#report-toc-rail").evaluate((node) => getComputedStyle(node).position), "sticky");
assert.equal(await page.locator("#answer-content .citation-link").count(), 6);
assert.equal(await page.locator("#answer-content .answer-body > ul li").count(), 3);

const originalHash = await page.evaluate(() => window.location.hash);
await page.locator("#report-toc-rail a").nth(1).click();
await page.waitForTimeout(100);
assert.equal(await page.evaluate(() => window.location.hash), originalHash);
assert.ok(await page.locator("#answer-content h3").nth(1).isVisible());

const collapsedFixture = `# 压缩表格回放

|阶段|名称|核心问题|优化对象 | 类比 | |-----|-----|--------|---------|-----| |第一代|提示词工程|怎么把话说清楚|Prompt 的措辞、格式、示例|表达技巧 | |第二代|上下文工程|怎么喂信息|文档、代码片段、历史对话|信息地图 | |第三代|Harness 工程|怎么让 Agent 可靠工作|约束、反馈回路、控制系统|可执行基础设施 |
`;
await page.evaluate(async (value) => {
  const { renderAnswer } = await import("/assets/modules/shared.js?v=v0.3-report-render-4");
  renderAnswer(document.querySelector("#answer-content"), value, "text/markdown");
}, collapsedFixture);
assert.equal(await page.locator("#answer-content .answer-table").count(), 1);
assert.equal(await page.locator("#answer-content .answer-table tbody tr").count(), 3);
assert.equal(await page.locator("#answer-content p").filter({ hasText: "|阶段|" }).count(), 0);
assert.deepEqual(errors, []);

console.log(JSON.stringify({ base_url: baseUrl, table_rows: 2, reference_items: 3, hash_preserved: true, console_errors: errors }));
await browser.close();
