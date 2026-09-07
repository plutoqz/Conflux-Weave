/* Workbench 共享工具：供研究视图（app.js）与总览 / 对话 / 设置视图复用。
   视觉语言与 API 合同遵循 UX-0 design-freeze；此处不引入任何外部依赖。 */

export const $ = (selector) => document.querySelector(selector);

export function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const sunIcon = document.querySelector("#theme-toggle .icon-sun");
  const moonIcon = document.querySelector("#theme-toggle .icon-moon");
  if (theme === "dark") {
    sunIcon?.setAttribute("hidden", "");
    moonIcon?.removeAttribute("hidden");
  } else {
    sunIcon?.removeAttribute("hidden");
    moonIcon?.setAttribute("hidden", "");
  }
}

export function initTheme() {
  const saved = localStorage.getItem("cw_theme");
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const initialTheme = saved || (prefersDark ? "dark" : "light");
  applyTheme(initialTheme);

  const toggle = $("#theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      localStorage.setItem("cw_theme", next);
    });
  }

  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
      if (!localStorage.getItem("cw_theme")) {
        applyTheme(e.matches ? "dark" : "light");
      }
    });
  }
}

function highlightCode(rawCode, lang) {
  const normLang = (lang || "").toLowerCase().trim();
  const escapeHtml = (str) =>
    str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  if (!normLang || /^(text|plain|txt|log)$/.test(normLang)) {
    return escapeHtml(rawCode);
  }

  let commentPattern = null;
  let kwPattern = null;

  if (/^(javascript|js|typescript|ts)$/.test(normLang)) {
    commentPattern = /\/\/[^\n]*|\/\*[\s\S]*?\*\//;
    kwPattern = /\b(const|let|var|function|return|if|else|for|while|import|export|from|class|extends|async|await|try|catch|finally|throw|new|this|typeof|instanceof|switch|case|default|break|continue|yield|null|undefined|true|false)\b/;
  } else if (/^(python|py)$/.test(normLang)) {
    commentPattern = /#[^\n]*/;
    kwPattern = /\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|raise|with|lambda|yield|async|await|pass|None|True|False|is|not|in|and|or|self)\b/;
  } else if (/^(json)$/.test(normLang)) {
    kwPattern = /\b(true|false|null)\b/;
  } else if (/^(bash|sh|shell|zsh)$/.test(normLang)) {
    commentPattern = /#[^\n]*/;
    kwPattern = /\b(if|then|else|elif|fi|case|esac|for|while|until|do|done|in|function|return|exit|local|export)\b/;
  } else if (/^(sql)$/.test(normLang)) {
    commentPattern = /--[^\n]*|\/\*[\s\S]*?\*\//;
    kwPattern = /\b(select|from|where|insert|into|update|delete|create|table|drop|alter|index|join|inner|left|right|full|on|group|by|order|limit|offset|having|union|all|distinct|as|and|or|not|null|is|in|values)\b/i;
  }

  const parts = [];
  if (commentPattern) parts.push(`(?<cmt>${commentPattern.source})`);
  parts.push('(?<str>"[^"\\\\]*(?:\\\\.[^"\\\\]*)*"|\'[^\'\\\\]*(?:\\\\.[^\'\\\\]*)*\'|`[^`\\\\]*(?:\\\\.[^`\\\\]*)*`)');
  if (kwPattern) parts.push(`(?<kw>${kwPattern.source})`);
  parts.push('(?<num>\\b\\d+(?:\\.\\d+)?\\b)');

  let combinedRegex;
  try {
    combinedRegex = new RegExp(parts.join("|"), "gm");
  } catch {
    return escapeHtml(rawCode);
  }

  let html = "";
  let lastIndex = 0;
  for (const match of rawCode.matchAll(combinedRegex)) {
    const idx = match.index;
    if (idx > lastIndex) {
      html += escapeHtml(rawCode.slice(lastIndex, idx));
    }
    const val = match[0];
    const groups = match.groups || {};
    if (groups.cmt) {
      html += `<span class="token-cmt">${escapeHtml(val)}</span>`;
    } else if (groups.str) {
      if (normLang === "json" && /:\s*$/.test(rawCode.slice(idx + val.length, idx + val.length + 5))) {
        html += `<span class="token-prop">${escapeHtml(val)}</span>`;
      } else {
        html += `<span class="token-str">${escapeHtml(val)}</span>`;
      }
    } else if (groups.kw) {
      html += `<span class="token-kw">${escapeHtml(val)}</span>`;
    } else if (groups.num) {
      html += `<span class="token-num">${escapeHtml(val)}</span>`;
    } else {
      html += escapeHtml(val);
    }
    lastIndex = idx + val.length;
  }
  if (lastIndex < rawCode.length) {
    html += escapeHtml(rawCode.slice(lastIndex));
  }
  return html;
}


export const stateLabels = {
  pending: "等待处理",
  working: "研究中",
  needs_attention: "需要决定",
  cancelling: "正在取消",
  complete: "已完成",
  partial: "部分完成",
  failed: "未完成",
  cancelled: "已取消",
  expired: "已过期",
};

export const familyLabels = {
  paper_discovery: "论文发现",
  research_fixture: "离线研究验证",
  verified_paper_research: "核验研究",
  managed_verified_research: "Manager 研究",
};

export const modeLabels = {
  discovery: "论文获取",
  single: "单 Agent",
  managed: "Manager 协作",
  fixture: "离线验证",
  direct: "直接问答",
  rag: "知识库问答",
  deep: "深度研究",
};

export async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(payload?.message || "请求未能完成。");
    error.recoveryAction = payload?.recovery_action;
    throw error;
  }
  return payload;
}

export function formatDate(value, detailed = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", detailed
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { month: "numeric", day: "numeric" }).format(date);
}

export function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 4200);
}

function appendInlineMarkdown(parent, value) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[(\d+)\]|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > cursor) parent.append(document.createTextNode(value.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else if (token.startsWith("*")) {
      const emphasis = document.createElement("em");
      emphasis.textContent = token.slice(1, -1);
      parent.append(emphasis);
    } else if (match[2]) {
      const link = document.createElement("a");
      link.href = `#citation-${match[2]}`;
      link.dataset.citationIndex = match[2];
      link.className = "citation-link";
      link.textContent = `[${match[2]}]`;
      link.setAttribute("aria-label", `打开引用 ${match[2]}`);
      parent.append(link);
    } else {
      const link = document.createElement("a");
      link.href = match[5];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[4];
      parent.append(link);
    }
    cursor = match.index + token.length;
  }
  if (cursor < value.length) parent.append(document.createTextNode(value.slice(cursor)));
}

function appendMarkdownText(parent, value) {
  appendInlineMarkdown(parent, value);
}

/* 交付文本渲染：把报告 Markdown 转成安全的结构化 DOM，正文优先展示。 */
export function renderAnswer(node, content, mediaType) {
  node.replaceChildren();
  if (!mediaType.startsWith("text/markdown")) {
    const rail = document.querySelector("#report-toc-rail");
    rail?.setAttribute("hidden", "");
    rail?.closest(".app-shell")?.setAttribute("data-toc", "closed");
    const body = document.createElement("p");
    body.className = "answer-paragraph";
    body.textContent = content || "";
    node.append(body);
    return;
  }
  const normalizedContent = String(content || "")
    .replace(/\\\|/g, "|")
    // Some providers collapse pipe tables into one line. Recover row boundaries
    // around the divider and preserve bold text inside ordinary paragraphs.
    // Providers sometimes collapse an entire pipe table into one line. Restore
    // the divider boundaries before block parsing; prose after the table stays intact.
    .replace(/(?:\|:?-{2,}:?){2,}\|?/g, (divider) => `\n${divider}\n`)
    // A collapsed table often uses `||` between rows. Recover row starts;
    // deliberate empty cells are not emitted by the report contract.
    .replace(/\|{2,}/g, "|\n|")
    .replace(/\|\s+\|/g, "|\n|");
  const normalizedLines = normalizedContent.replace(/\r\n?/g, "\n").split("\n");
  const lines = [];
  let inReferences = false;
  for (let index = 0; index < normalizedLines.length; index += 1) {
    const line = normalizedLines[index];
    const isHeadingLine = /^#{1,6}\s+/.test(line);
    if (/^#{1,6}\s+(?:来源引用|来源)\s*$/.test(line)) inReferences = true;
    else if (/^#{1,6}\s+/.test(line) && !/来源/.test(line)) inReferences = false;
    if (inReferences && !isHeadingLine && line.trim() && !/^\s*[-*+]\s+/.test(line)) {
      const parts = line.split(/\s+(?=\[\d+\](?:\(|《))/);
      parts.forEach((part) => lines.push(`- ${part.trim()}`));
    } else {
      lines.push(line);
    }
  }
  const body = document.createElement("div");
  body.className = "answer-body";
  let paragraph = [];
  let list = null;
  let quote = null;
  let callout = null;
  let code = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    appendMarkdownText(p, paragraph.join(" ").trim());
    body.append(p);
    paragraph = [];
  };
  const flushList = () => { if (list) { body.append(list); list = null; } };
  const flushQuote = () => { if (quote) { body.append(quote); quote = null; } };
  const flushCallout = () => { if (callout) { body.append(callout); callout = null; } };
  const flushCode = () => {
    if (!code) return;
    const wrapper = document.createElement("div");
    wrapper.className = "code-block";
    const header = document.createElement("div");
    header.className = "code-block-header";
    const langSpan = document.createElement("span");
    langSpan.className = "code-lang";
    langSpan.textContent = code.lang || "code";
    const copyBtn = document.createElement("button");
    copyBtn.className = "code-copy-btn";
    copyBtn.type = "button";
    copyBtn.title = "复制代码";
    copyBtn.setAttribute("aria-label", "复制代码");
    copyBtn.innerHTML = '<svg class="icon icon-copy" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg><svg class="icon icon-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" hidden><polyline points="20 6 9 17 4 12"/></svg><span>复制</span>';
    const fullCode = code.lines.join("\n");
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(fullCode);
        copyBtn.querySelector(".icon-copy")?.setAttribute("hidden", "");
        copyBtn.querySelector(".icon-check")?.removeAttribute("hidden");
        const span = copyBtn.querySelector("span");
        if (span) span.textContent = "已复制";
        copyBtn.classList.add("copied");
        setTimeout(() => {
          copyBtn.querySelector(".icon-copy")?.removeAttribute("hidden");
          copyBtn.querySelector(".icon-check")?.setAttribute("hidden", "");
          if (span) span.textContent = "复制";
          copyBtn.classList.remove("copied");
        }, 2000);
      } catch (err) {
        showToast("复制失败");
      }
    });
    header.append(langSpan, copyBtn);
    const pre = document.createElement("pre");
    const codeNode = document.createElement("code");
    codeNode.innerHTML = highlightCode(fullCode, code.lang);
    pre.append(codeNode);
    wrapper.append(header, pre);
    body.append(wrapper);
    code = null;
  };
  const parseTableRow = (line) => {
    // URL/title lines may contain pipes; only parse deliberate Markdown table rows.
    if (/https?:\/\//i.test(line)) return null;
    const normalized = line.replace(/^\s*[○•]\s*/, "").trim().replace(/^\\\|/, "|").replace(/\\\|\s*$/, "|");
    if (!normalized.startsWith("|") || (normalized.match(/\|/g) || []).length < 2) return null;
    const cells = normalized.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.replace(/\\\|/g, "|").trim());
    return cells.length >= 2 ? cells : null;
  };
  const isTableDivider = (cells) => cells.length >= 2 && cells.every((cell) => /^:?-{2,}:?$/.test(cell.replace(/\s/g, "")));
  const flushTable = () => {
    if (!table) return;
    const tableNode = document.createElement("table");
    tableNode.className = "answer-table";
    const head = document.createElement("thead");
    const bodyNode = document.createElement("tbody");
    table.rows[0].forEach((cell) => { const th = document.createElement("th"); appendMarkdownText(th, cell); head.append(th); });
    table.rows.slice(1).forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((cell) => { const td = document.createElement("td"); appendMarkdownText(td, cell); tr.append(td); });
      bodyNode.append(tr);
    });
    tableNode.append(head, bodyNode);
    const tableWrap = document.createElement("div");
    tableWrap.className = "answer-table-wrap";
    tableWrap.append(tableNode);
    body.append(tableWrap); table = null;
  };
  let table = null;
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const line = lines[lineIndex];
    if (line.startsWith("```")) {
      flushParagraph(); flushList(); flushQuote(); flushCallout(); flushTable();
      if (code) flushCode(); else code = { lang: line.slice(3).trim(), lines: [] };
      continue;
    }
    if (code) { code.lines.push(line); continue; }
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
    if (heading) {
      flushParagraph(); flushList(); flushQuote(); flushCallout(); flushTable();
      const h = document.createElement(`h${Math.min(heading[1].length + 1, 6)}`);
      h.id = heading[2].toLowerCase().replace(/[^\u3400-\u9fff\w\s-]/g, "").trim().replace(/[\s_-]+/g, "-") || `section-${body.querySelectorAll("h2,h3,h4,h5,h6").length + 1}`;
      appendMarkdownText(h, heading[2]);
      body.append(h);
      continue;
    }
    const tableRow = parseTableRow(line);
    if (tableRow) {
      if (!table) {
        const nextRow = lineIndex + 1 < lines.length ? parseTableRow(lines[lineIndex + 1]) : null;
        if (nextRow && isTableDivider(nextRow)) {
          flushParagraph(); flushList(); flushQuote(); flushCallout();
          table = { rows: [tableRow], columns: tableRow.length };
          lineIndex += 1;
          continue;
        }
      } else if (!isTableDivider(tableRow)) {
        flushParagraph(); flushList(); flushQuote(); flushCallout();
        const columns = table.columns || table.rows[0].length;
        if (tableRow.length > columns) {
          const completeCells = Math.floor(tableRow.length / columns) * columns;
          for (let offset = 0; offset < completeCells; offset += columns) {
            table.rows.push(tableRow.slice(offset, offset + columns));
          }
          const remainder = tableRow.slice(completeCells).filter((cell) => cell.trim()).join(" ");
          flushTable();
          if (remainder) paragraph.push(remainder);
        } else {
          table.rows.push(tableRow);
        }
        continue;
      }
    }
    if (table) flushTable();
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { flushParagraph(); flushList(); flushQuote(); flushCallout(); body.append(document.createElement("hr")); continue; }
    const quoteLine = line.match(/^\s*>\s?(.*)$/);
    if (quoteLine) {
      flushParagraph(); flushList();
      const calloutMarker = quoteLine[1].match(/^\[!(NOTE|TIP|WARNING|IMPORTANT)\]\s*$/i);
      if (calloutMarker) {
        flushQuote(); flushCallout();
        callout = document.createElement("aside");
        callout.className = `report-callout ${calloutMarker[1].toLowerCase()}`;
        callout.setAttribute("role", "note");
        const label = document.createElement("strong");
        label.className = "report-callout-label";
        label.textContent = calloutMarker[1].toUpperCase();
        callout.append(label);
        continue;
      }
      if (callout) {
        const p = document.createElement("p");
        appendMarkdownText(p, quoteLine[1]);
        callout.append(p);
        continue;
      }
      if (!quote) { quote = document.createElement("blockquote"); }
      const p = document.createElement("p"); appendMarkdownText(p, quoteLine[1]); quote.append(p); continue;
    }
    const item = line.match(/^\s*([-*+] |\d+\. )(.+)$/);
    if (item) {
      flushParagraph(); flushQuote(); flushCallout();
      const ordered = /^\d/.test(item[1]);
      if (!list || list.tagName.toLowerCase() !== (ordered ? "ol" : "ul")) { flushList(); list = document.createElement(ordered ? "ol" : "ul"); }
      const li = document.createElement("li"); appendMarkdownText(li, item[2]); list.append(li); continue;
    }
    if (!line.trim()) { flushParagraph(); flushList(); flushQuote(); flushCallout(); flushTable(); continue; }
    flushList(); flushQuote(); flushCallout(); paragraph.push(line.replace(/^\s*[○•]\s*/, "").trim());
  }
  flushParagraph(); flushList(); flushQuote(); flushCallout(); flushCode(); flushTable();
  const headings = [...body.querySelectorAll("h2, h3, h4, h5, h6")];
  if (headings.length >= 2) {
    const toc = document.createElement("nav");
    toc.className = "report-toc";
    toc.setAttribute("aria-label", "报告目录");
    const tocTitle = document.createElement("strong");
    tocTitle.textContent = "报告目录";
    toc.append(tocTitle);
    const listNode = document.createElement("ul");
    headings.forEach((heading) => {
      const itemNode = document.createElement("li");
      itemNode.dataset.level = heading.tagName.slice(1);
      const link = document.createElement("a");
      link.href = `#${heading.id}`;
      link.textContent = heading.textContent;
      link.title = heading.textContent;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        document.getElementById(heading.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      itemNode.append(link); listNode.append(itemNode);
    });
    toc.append(listNode);
    const rail = node.id === "answer-content" ? document.querySelector("#report-toc-rail") : null;
    if (rail) {
      rail.replaceChildren(toc);
      rail.hidden = false;
      rail.closest(".app-shell")?.setAttribute("data-toc", "open");
    } else {
      node.append(toc);
    }
    if ("IntersectionObserver" in window) {
      const links = new Map(headings.map((heading) => [heading.id, listNode.querySelector(`a[href="#${heading.id}"]`)]));
      const observer = new IntersectionObserver((entries) => {
        entries.filter((entry) => entry.isIntersecting).forEach((entry) => {
          links.forEach((link) => link?.removeAttribute("aria-current"));
          links.get(entry.target.id)?.setAttribute("aria-current", "location");
        });
      }, { rootMargin: "-88px 0px -65% 0px", threshold: 0 });
      headings.forEach((heading) => observer.observe(heading));
    }
  }
  const rail = document.querySelector("#report-toc-rail");
  if (rail && headings.length < 2) {
    rail.hidden = true;
    rail.closest(".app-shell")?.setAttribute("data-toc", "closed");
  }
  node.append(body);
}

/* 系统检查行：总览与设置共用同一真实数据源 /api/v1/health/ready。 */
const checkNames = {
  database: "运行数据库",
  artifact_store: "工件库",
  provider: "模型服务",
};

export function renderChecks(container, checks) {
  const rows = checks.map((check) => {
    const row = document.createElement("div");
    row.className = `check-row ${check.status}`;
    const head = document.createElement("div");
    head.className = "check-head";
    const label = document.createElement("strong");
    label.textContent = checkNames[check.name] || check.name;
    const state = document.createElement("span");
    state.className = "check-state";
    state.textContent = check.status === "ready" ? "就绪" : "未就绪";
    head.append(label, state);
    const body = document.createElement("p");
    body.textContent = check.message;
    row.append(head, body);
    if (check.recovery_action) {
      const action = document.createElement("p");
      action.className = "check-action";
      action.textContent = `建议：${check.recovery_action}`;
      row.append(action);
    }
    return row;
  });
  container.replaceChildren(...rows);
  container.hidden = !checks.length;
}
