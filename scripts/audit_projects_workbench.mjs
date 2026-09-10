import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");

const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8799";
const outputRoot = path.resolve("var/acceptance/v0.3-p3/layout_audit");
await fs.mkdir(outputRoot, { recursive: true });

const consoleErrors = [];
const consoleWarnings = [];

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
  if (msg.type() === "warning") consoleWarnings.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push(err.message));

console.log("=== 1. 加载项目工作台 ===");
await page.goto(`${baseUrl}/#/projects`, { waitUntil: "networkidle" });
await page.locator("#projects-view").waitFor({ state: "visible", timeout: 10000 });

// 确认当前处于 projects 分区
const activeSection = await page.evaluate(() => document.querySelector(".app-shell")?.dataset.section);
assert.equal(activeSection, "projects", "app-shell dataset.section should be projects");

// 等待项目选择框与数据加载完成
await page.waitForFunction(() => {
  const sel = document.querySelector("#project-select");
  return sel && sel.options.length > 0;
});
const projectName = await page.evaluate(() => document.querySelector("#project-select")?.selectedOptions[0]?.textContent);
console.log(`当前加载项目: ${projectName}`);

// 等待 Git 状态卡片渲染
await page.locator("#project-git-card").waitFor({ state: "visible" });
const gitBranch = await page.locator("#proj-git-branch").innerText();
console.log(`Git 分支: ${gitBranch}`);

// 等待目录树渲染
await page.locator("#project-file-tree .tree-node").first().waitFor({ state: "visible", timeout: 10000 });
const treeNodesCount = await page.locator("#project-file-tree .tree-node").count();
console.log(`目录树节点数量: ${treeNodesCount}`);

// === 布局审计辅助函数 ===
async function runLayoutChecks(stageLabel) {
  const result = await page.evaluate(() => {
    const report = {
      label: "",
      docOverflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      bodyOverflowX: document.body.scrollWidth - document.body.clientWidth,
      stageOverflowX: 0,
      sidebarOverflowX: 0,
      agentPanelOverflowX: 0,
      clippedElements: [],
    };

    const stage = document.querySelector(".projects-stage");
    if (stage) report.stageOverflowX = stage.scrollWidth - stage.clientWidth;

    const sidebar = document.querySelector(".projects-sidebar");
    if (sidebar) report.sidebarOverflowX = sidebar.scrollWidth - sidebar.clientWidth;

    const agent = document.querySelector(".projects-agent-panel");
    if (agent) report.agentPanelOverflowX = agent.scrollWidth - agent.clientWidth;

    // 检查文字截断或容器子元素异常超出的情况
    const textEls = [...document.querySelectorAll("#projects-view *")].filter((el) => {
      if (!(el instanceof HTMLElement)) return false;
      const s = getComputedStyle(el);
      if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") return false;
      return [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim().length > 0);
    });

    for (const el of textEls) {
      if (el.scrollWidth > el.clientWidth + 2 && !el.classList.contains("project-code-viewer") && !el.classList.contains("finding-snippet") && !el.classList.contains("project-diff-viewer") && !el.classList.contains("code-line-content")) {
        const textPreview = el.textContent.trim().slice(0, 30);
        report.clippedElements.push({
          tag: el.tagName,
          class: el.className,
          text: textPreview,
          clipX: el.scrollWidth - el.clientWidth,
        });
      }
    }

    return report;
  });

  result.label = stageLabel;
  return result;
}

const auditResults = [];

// === 2. 检查 Center Tab 1: 架构全景与拓扑 ===
console.log("=== 2. 检查架构全景与拓扑 Tab ===");
await page.locator("#tab-center-arch").click();
await page.locator("#panel-center-arch").waitFor({ state: "visible" });
await page.locator("#walkthrough-topology-container svg").waitFor({ state: "visible", timeout: 10000 });
const topoNodesCount = await page.locator(".topology-node").count();
console.log(`SVG 架构拓扑节点数量: ${topoNodesCount}`);
assert.ok(topoNodesCount >= 4, "Topology diagram should have at least 4 nodes");

const compCardsCount = await page.locator(".component-card").count();
console.log(`分层组件卡片数量: ${compCardsCount}`);
assert.ok(compCardsCount >= 3, "Components grid should have at least 3 component cards");

await page.screenshot({ path: path.join(outputRoot, "01-tab-architecture-1440.png") });
auditResults.push(await runLayoutChecks("01-tab-architecture-1440"));

// === 3. 检查 Center Tab 2: 论文理论映射 ===
console.log("=== 3. 检查论文理论映射 Tab ===");
await page.locator("#tab-center-theory").click();
await page.locator("#panel-center-theory").waitFor({ state: "visible" });
await page.locator(".theory-mapping-card").first().waitFor({ state: "visible", timeout: 10000 });
const theoryCardsCount = await page.locator(".theory-mapping-card").count();
console.log(`理论映射卡片数量: ${theoryCardsCount}`);
assert.ok(theoryCardsCount >= 3, "Theory mappings should have at least 3 cards");

await page.screenshot({ path: path.join(outputRoot, "02-tab-theory-1440.png") });
auditResults.push(await runLayoutChecks("02-tab-theory-1440"));

// 测试从理论映射跳转至代码研读台
console.log("测试理论映射点击跳转定位源码...");
const firstJumpBtn = page.locator(".theory-mapping-card .btn-jump-code").first();
const jumpFile = await firstJumpBtn.getAttribute("data-file");
const jumpLine = await firstJumpBtn.getAttribute("data-line");
console.log(`跳转目标: ${jumpFile}:${jumpLine}`);
await firstJumpBtn.click();

// 验证中台自动切换到 code tab 并高亮代码行
await page.locator("#panel-center-code").waitFor({ state: "visible" });
const activeTabCode = await page.locator("#tab-center-code").getAttribute("class");
assert.ok(activeTabCode.includes("active"), "tab-center-code should become active after jump");

await page.locator(".code-line-row.highlight-line").waitFor({ state: "visible", timeout: 5000 });
const highlightedLineNum = await page.locator(".code-line-row.highlight-line .code-line-num").innerText();
console.log(`实际高亮行号: ${highlightedLineNum.trim()}`);
assert.equal(highlightedLineNum.trim(), jumpLine, "highlighted line should match jump line");
await page.screenshot({ path: path.join(outputRoot, "03-tab-code-highlight-1440.png") });

// === 4. 检查 Center Tab 3: 契约审计与体检 ===
console.log("=== 4. 检查契约审计与体检 Tab ===");
await page.locator("#tab-center-audit").click();
await page.locator("#panel-center-audit").waitFor({ state: "visible" });
await page.locator("#audit-score-impl").waitFor({ state: "visible" });

const implScore = await page.locator("#audit-score-impl").innerText();
const healthScore = await page.locator("#audit-score-health").innerText();
console.log(`实现得分: ${implScore}, 健康得分: ${healthScore}`);

const findingsCount = await page.locator(".audit-finding-item").count();
console.log(`审计发现条目数: ${findingsCount}`);

await page.screenshot({ path: path.join(outputRoot, "04-tab-audit-1440.png") });
auditResults.push(await runLayoutChecks("04-tab-audit-1440"));

// 测试点击“生成解耦补丁”按钮联动右侧面板
if (findingsCount > 0) {
  console.log("测试审计条目的'生成解耦补丁'联动...");
  await page.locator(".audit-finding-item .btn-decouple-finding").first().click();
  await page.locator("#proj-panel-coding").waitFor({ state: "visible" });
  const targetVal = await page.locator("#proj-coding-target").inputValue();
  const instVal = await page.locator("#proj-coding-instruction").inputValue();
  console.log(`右侧解耦抽屉联动填入: target=${targetVal}, instruction=${instVal.slice(0, 30)}...`);
  assert.ok(targetVal.length > 0, "target file should be filled in");
}

// === 5. 检查 Git 分支语义差异 Dialog ===
console.log("=== 5. 检查 Git 分支语义差异弹窗 ===");
await page.locator("#proj-btn-semantic-diff").click();
const diffDialog = page.locator("#project-semantic-diff-dialog");
await diffDialog.waitFor({ state: "visible" });
await page.waitForFunction(() => {
  const el = document.querySelector("#diff-impact-level");
  return el && el.textContent !== "计算中" && el.textContent !== "";
}, { timeout: 10000 });
const diffImpact = await page.locator("#diff-impact-level").innerText();
const diffIntent = await page.locator("#diff-intent-text").innerText();
console.log(`语义差异等级: ${diffImpact}, 意图: ${diffIntent.slice(0, 40)}...`);
await page.screenshot({ path: path.join(outputRoot, "05-semantic-diff-dialog-1440.png") });

// 关闭弹窗
await page.locator("#semantic-diff-close").click();
await page.waitForTimeout(300);

// === 5.1 测试 ProjectAgent 问答与快速 Chips ===
console.log("测试 ProjectAgent 快速提问与问答渲染...");
const quickChip = page.locator(".quick-chip-btn").first();
await quickChip.click();
await page.locator("#proj-panel-qa").waitFor({ state: "visible" });
await page.locator("#proj-qa-answer").waitFor({ state: "visible", timeout: 10000 });
await page.waitForFunction(() => {
  const ans = document.querySelector("#proj-qa-answer");
  return ans && !ans.textContent.includes("正在分析") && ans.textContent.trim().length > 0;
}, { timeout: 10000 });
const answerSnippet = (await page.locator("#proj-qa-answer").innerText()).slice(0, 40);
console.log(`问答渲染结果片段: ${answerSnippet}...`);
await page.screenshot({ path: path.join(outputRoot, "05b-proj-qa-result.png") });

// === 6. 响应式布局测试：1024x768 (平板/窄屏笔记本) ===
console.log("=== 6. 测试 1024x768 响应式布局 ===");
await page.setViewportSize({ width: 1024, height: 768 });
await page.waitForTimeout(500);
await page.screenshot({ path: path.join(outputRoot, "06-responsive-1024.png") });
auditResults.push(await runLayoutChecks("06-responsive-1024"));

// === 7. 响应式布局测试：390x844 (移动端) ===
console.log("=== 7. 测试 390x844 移动端布局 ===");
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(500);
await page.screenshot({ path: path.join(outputRoot, "07-responsive-390.png") });
auditResults.push(await runLayoutChecks("07-responsive-390"));

// 恢复桌面视口
await page.setViewportSize({ width: 1440, height: 900 });

// === 8. 输出综合报告 ===
await browser.close();

console.log("\n=== 审计结果摘要 ===");
console.log(`Console 错误数: ${consoleErrors.length}`);
if (consoleErrors.length > 0) {
  console.log("Console 错误详情:", consoleErrors);
}
console.log(`Console 警告数: ${consoleWarnings.length}`);

for (const r of auditResults) {
  console.log(`\n[${r.label}]`);
  console.log(`  - 页面整体横向溢出: doc=${r.docOverflowX}px, body=${r.bodyOverflowX}px`);
  console.log(`  - 容器横向溢出: stage=${r.stageOverflowX}px, sidebar=${r.sidebarOverflowX}px, agentPanel=${r.agentPanelOverflowX}px`);
  if (r.clippedElements.length > 0) {
    console.log(`  - 截断/超出元素数: ${r.clippedElements.length}`);
    r.clippedElements.slice(0, 5).forEach((c) => {
      console.log(`    * <${c.tag} class="${c.class}">: clipX=${c.clipX}px, text="${c.text}"`);
    });
  } else {
    console.log("  - 无异常截断元素");
  }
}

await fs.writeFile(
  path.join(outputRoot, "audit_summary.json"),
  JSON.stringify({ consoleErrors, consoleWarnings, auditResults }, null, 2),
  "utf-8"
);
console.log("\n审计脚本执行完成，截图与摘要已保存至:", outputRoot);
