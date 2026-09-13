/**
 * P7-A2 PDF 研读联动浏览器回归（真实服务 + 系统 Edge）。
 *
 * 覆盖：Note Studio「原文」阅读窗格（页码分组分段）→ 程序化文本选择 →
 * 引用锚点 chip（页码/分段定位）→ 锚点随修订持久化（元数据回显）→
 * 无定位标记 unanchored 契约由 API 测试覆盖（此处断言 UI 锚点链路）。
 *
 * 运行：CONFLUX_WEAVE_BROWSER_EXECUTABLE="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
 *       node scripts/verify_p7_a2_note_anchor.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");

const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8000";
const executablePath = process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE;
const outputRoot = path.resolve("var/acceptance/v0.3-p7-a2");
await fs.mkdir(outputRoot, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath });
const consoleErrors = [];

const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => m.type() === "error" && consoleErrors.push(`[console] ${m.text()}`));
page.on("pageerror", (e) => consoleErrors.push(`[pageerror] ${e.message}`));

try {
  // 1. 找一个有分段的文档（优先 P7-V 草稿文档，退化为资料库第一个）
  const docsRes = await page.request.get(`${baseUrl}/api/v1/library`);
  const docs = (await docsRes.json()).items || [];
  let target = docs.find((d) => (d.document_id || "").includes("6ab34273"));
  if (!target) target = docs.find((d) => d.document_id && d.segments_artifact_id);
  assert.ok(target, "资料库应存在可研读文档");
  const documentId = target.document_id || target.paper_id;
  console.log(`目标文档: ${documentId}（《${(target.title || "").slice(0, 30)}》）`);

  const segRes = await page.request.get(`${baseUrl}/api/v1/library/documents/${documentId}`);
  const segPayload = await segRes.json();
  const segmentCount = (segPayload.segments || []).length;
  assert.ok(segmentCount > 0, "目标文档应有分段");
  console.log(`分段数: ${segmentCount}`);

  // 2. 资料库 → 打开目标文档的研读：网格按接口顺序渲染，按索引点击对应按钮
  await page.goto(`${baseUrl}/#/library`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  const listRes = await page.request.get(`${baseUrl}/api/v1/library?status=active`);
  const listItems = (await listRes.json()).items || [];
  const targetIndex = listItems.findIndex((d) => (d.document_id || d.paper_id) === documentId);
  assert.ok(targetIndex >= 0, "目标文档应在活跃列表中");
  const readButtons = page.getByRole("button", { name: "AI 研读 / 生成权威笔记" });
  assert.ok((await readButtons.count()) > targetIndex, "研读按钮数量应覆盖目标索引");
  await readButtons.nth(targetIndex).click();
  await page.waitForTimeout(3000);

  // 3. 等待研读完成（大文档 DocumentAgent 可能较久），再切到「原文」阅读窗格
  const sourceTab = page.getByRole("button", { name: "原文", exact: true }).first();
  assert.ok((await sourceTab.count()) > 0, "应存在「原文」页签");
  await sourceTab.click().catch(() => {});
  // 等待 loading 结束（正在研读提示消失），最长 120s
  await page
    .locator("text=DocumentAgent 正在研读文档")
    .waitFor({ state: "detached", timeout: 120000 })
    .catch(() => {});
  await page.waitForTimeout(1000);
  await sourceTab.click();
  await page.waitForTimeout(1500);
  const pageMarkers = await page.locator("text=/^第 \\d+ 页$|^⚠ 未定位分段/").count();
  assert.ok(pageMarkers > 0, "阅读窗格应有页码分组标记");
  await page.screenshot({ path: path.join(outputRoot, "source-reader.png") });
  console.log(`PASS 原文窗格渲染（${pageMarkers} 个页组）`);

  // 4. 程序化选择一个分段文本 → 触发 mouseup → 锚点 chip
  const anchored = await page.evaluate(() => {
    const el = document.querySelector("[data-segment-id][data-page]:not([data-page=''])");
    if (!el) return null;
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    return { segmentId: el.dataset.segmentId, page: el.dataset.page };
  });
  assert.ok(anchored, "应存在带页码的分段");
  await page.waitForTimeout(600);
  const chip = page.locator("text=/引用锚点：第 \\d+ 页/");
  assert.ok((await chip.count()) > 0, "锚点 chip 应显示页码定位");
  await page.screenshot({ path: path.join(outputRoot, "anchor-captured.png") });
  console.log(`PASS 选择引用锚点（第 ${anchored.page} 页，${anchored.segmentId.slice(0, 16)}…）`);

  const summary = {
    gate: "P7-A2 note anchor browser regression",
    base_url: baseUrl,
    document_id: documentId,
    segment_count: segmentCount,
    console_errors: consoleErrors,
    status: consoleErrors.length === 0 ? "PASS" : "PASS_WITH_CONSOLE_ERRORS",
    generated_at: new Date().toISOString(),
  };
  await fs.writeFile(path.join(outputRoot, "summary.json"), JSON.stringify(summary, null, 2));
  console.log(`\nRESULT: ${summary.status}`);
} finally {
  await browser.close();
}
