/* Workbench 共享工具：供研究视图（app.js）与总览 / 对话 / 设置视图复用。
   视觉语言与 API 合同遵循 UX-0 design-freeze；此处不引入任何外部依赖。 */

export const $ = (selector) => document.querySelector(selector);

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
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))/g;
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
    } else {
      const link = document.createElement("a");
      link.href = match[3];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[2];
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
    const body = document.createElement("p");
    body.className = "answer-paragraph";
    body.textContent = content || "";
    node.append(body);
    return;
  }
  const lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
  const body = document.createElement("div");
  body.className = "answer-body";
  let paragraph = [];
  let list = null;
  let quote = null;
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
  const flushCode = () => {
    if (!code) return;
    const pre = document.createElement("pre");
    const codeNode = document.createElement("code");
    codeNode.textContent = code.lines.join("\n");
    pre.append(codeNode);
    body.append(pre);
    code = null;
  };
  for (const line of lines) {
    if (line.startsWith("```")) {
      flushParagraph(); flushList(); flushQuote();
      if (code) flushCode(); else code = { lines: [] };
      continue;
    }
    if (code) { code.lines.push(line); continue; }
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
    if (heading) {
      flushParagraph(); flushList(); flushQuote();
      const h = document.createElement(`h${Math.min(heading[1].length + 1, 6)}`);
      appendMarkdownText(h, heading[2]);
      body.append(h);
      continue;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { flushParagraph(); flushList(); flushQuote(); body.append(document.createElement("hr")); continue; }
    const quoteLine = line.match(/^\s*>\s?(.*)$/);
    if (quoteLine) {
      flushParagraph(); flushList();
      if (!quote) { quote = document.createElement("blockquote"); }
      const p = document.createElement("p"); appendMarkdownText(p, quoteLine[1]); quote.append(p); continue;
    }
    const item = line.match(/^\s*([-*+] |\d+\. )(.+)$/);
    if (item) {
      flushParagraph(); flushQuote();
      const ordered = /^\d/.test(item[1]);
      if (!list || list.tagName.toLowerCase() !== (ordered ? "ol" : "ul")) { flushList(); list = document.createElement(ordered ? "ol" : "ul"); }
      const li = document.createElement("li"); appendMarkdownText(li, item[2]); list.append(li); continue;
    }
    if (!line.trim()) { flushParagraph(); flushList(); flushQuote(); continue; }
    flushList(); flushQuote(); paragraph.push(line.trim());
  }
  flushParagraph(); flushList(); flushQuote(); flushCode();
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
