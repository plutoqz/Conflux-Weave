// UX-0.1 布局审计：DOM 级重叠 / 遮挡 / 溢出 / 截断检测
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("tools/ux0-browser/package.json"));
const { chromium } = require("playwright");

const baseUrl = process.env.CONFLUX_WEAVE_WORKBENCH_URL || "http://127.0.0.1:8765";
const executablePath = process.env.CONFLUX_WEAVE_BROWSER_EXECUTABLE;
const outputRoot = path.resolve("var/acceptance/v0.3-ux0/audit");
await fs.mkdir(outputRoot, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(baseUrl, { waitUntil: "networkidle" });
await page.locator("#run-state").waitFor({ state: "visible" });

const audit = await page.evaluate(() => {
  const report = { occlusions: [], clippedText: [], overflowContainers: [], offViewport: [] };

  // 1) 文字遮挡：对每个含直接文本的可见元素，采样其中心点与四角，
  //    若 elementFromPoint 命中的元素不是自身/后代/祖先，则记录为被遮挡。
  const textElements = [...document.querySelectorAll("body *")].filter((el) => {
    if (!(el instanceof HTMLElement)) return false;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    const text = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim().length > 0);
    return text;
  });

  const seen = new Set();
  for (const el of textElements) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const points = [
      [rect.left + rect.width / 2, rect.top + rect.height / 2],
      [rect.left + 2, rect.top + 2],
      [rect.right - 2, rect.bottom - 2],
    ];
    for (const [x, y] of points) {
      if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
      const hit = document.elementFromPoint(x, y);
      if (!hit) continue;
      const isSelfOrFamily = el.contains(hit) || hit.contains(el);
      if (!isSelfOrFamily) {
        const key = `${el.tagName}.${el.className}|${hit.tagName}.${hit.className}`;
        if (!seen.has(key)) {
          seen.add(key);
          report.occlusions.push({
            text: el.textContent.trim().slice(0, 40),
            element: `${el.tagName}.${String(el.className).slice(0, 40)}`,
            coveredBy: `${hit.tagName}.${String(hit.className).slice(0, 40)}`,
            at: [Math.round(x), Math.round(y)],
          });
        }
        break;
      }
    }
  }

  // 2) 文字截断：scrollWidth/scrollHeight 超过 clientWidth/clientHeight 的可见文字元素
  for (const el of textElements) {
    const clippedX = el.scrollWidth > el.clientWidth + 1;
    const clippedY = el.scrollHeight > el.clientHeight + 1;
    if (clippedX || clippedY) {
      report.clippedText.push({
        text: el.textContent.trim().slice(0, 40),
        element: `${el.tagName}.${String(el.className).slice(0, 40)}`,
        clipX: el.scrollWidth - el.clientWidth,
        clipY: el.scrollHeight - el.clientHeight,
        ellipsis: getComputedStyle(el).textOverflow,
      });
    }
  }

  // 3) 容器内部溢出：子元素超出容器 padding box（典型布局 bug）
  for (const container of document.querySelectorAll(".run-sidebar, .hud, .run-item, .sidebar-heading, .topbar, .app-shell")) {
    const cRect = container.getBoundingClientRect();
    for (const child of container.children) {
      const r = child.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      const outRight = r.right - cRect.right;
      const outBottom = r.bottom - cRect.bottom;
      const outLeft = cRect.left - r.left;
      const outTop = cRect.top - r.top;
      if (outRight > 1 || outBottom > 1 || outLeft > 1 || outTop > 1) {
        report.overflowContainers.push({
          container: `${container.tagName}.${String(container.className).slice(0, 30)}`,
          child: `${child.tagName}.${String(child.className).slice(0, 30)}`,
          outLeft: Math.round(outLeft), outRight: Math.round(outRight),
          outTop: Math.round(outTop), outBottom: Math.round(outBottom),
        });
      }
    }
  }

  // 4) 侧栏实际几何：供人工判断
  const sidebar = document.querySelector(".run-sidebar");
  const heading = document.querySelector(".sidebar-heading");
  const hud = document.querySelector(".hud");
  const runList = document.querySelector("#run-list");
  const firstItem = document.querySelector(".run-item");
  const geom = (el) => el ? (() => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; })() : null;
  report.geometry = {
    sidebar: geom(sidebar),
    heading: geom(heading),
    hud: geom(hud),
    runList: geom(runList),
    firstItem: geom(firstItem),
    sidebarScroll: sidebar ? { scrollH: sidebar.scrollHeight, clientH: sidebar.clientHeight, overflow: getComputedStyle(sidebar).overflow } : null,
  };
  return report;
});

console.log(JSON.stringify(audit, null, 2));
await fs.writeFile(path.join(outputRoot, "layout-audit.json"), JSON.stringify(audit, null, 2) + "\n", "utf8");

// 侧栏特写截图（2x 缩放便于肉眼复核）
const sidebar = page.locator(".run-sidebar");
await sidebar.screenshot({ path: path.join(outputRoot, "sidebar-closeup.png") });
const hudOpen = page.locator("#hud-toggle");
await hudOpen.click();
await page.waitForTimeout(200);
await sidebar.screenshot({ path: path.join(outputRoot, "sidebar-closeup-hud-open.png") });
await browser.close();
