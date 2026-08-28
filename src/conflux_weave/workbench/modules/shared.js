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

/* 交付文本渲染：与 Run 视图一致，仅提取首行标题，不做 Markdown 渲染。 */
export function renderAnswer(node, content, mediaType) {
  node.replaceChildren();
  if (mediaType.startsWith("text/markdown")) {
    const [firstLine, ...remaining] = content.split("\n");
    if (firstLine.startsWith("# ")) {
      const heading = document.createElement("h2");
      heading.textContent = firstLine.slice(2).trim();
      node.append(heading);
      content = remaining.join("\n").trim();
    }
  }
  if (content) {
    const body = document.createElement("div");
    body.className = "answer-body";
    body.textContent = content;
    node.append(body);
  }
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
