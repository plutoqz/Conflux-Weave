/* 统一对话入口（v1.1 线程化）：提问即创建核验研究 Run，事件流与交付以对话形式呈现。
   历史按 parent_run_id 追溯为只读线程（UX-1.1）：不删除、不改审计语义；
   "新对话"仅清空当前前端视图。P4 统一对话路由就绪后仅替换提交入口，UI 合同不变。 */

import { api, familyLabels, formatDate, modeLabels, renderAnswer, showToast, stateLabels } from "./shared.js?v=v0.3-report-render-4";
import { registerView } from "./router.js";

const TERMINAL_STATES = new Set(["complete", "partial", "failed", "cancelled", "expired"]);
const VERIFIED_FAMILIES = new Set(["verified_paper_research", "managed_verified_research"]);
const HISTORY_RUN_LIMIT = 20;
const HISTORY_DETAIL_LIMIT = 12;
const DEFAULT_PLACEHOLDER = "输入问题或命令（如 /deep、/doc、@project:...），Enter 发送，Shift+Enter 换行";

const thread = document.getElementById("chat-thread");
const threadEmpty = document.getElementById("chat-thread-empty");
const historyPanel = document.getElementById("chat-history");
const historyThreads = document.getElementById("chat-history-threads");
const turnNav = document.getElementById("chat-turn-nav");
const turnList = document.getElementById("chat-turn-list");
const input = document.getElementById("chat-input");
const errorNode = document.getElementById("chat-error");
const sendButton = document.getElementById("chat-send");
const autocomplete = document.getElementById("chat-autocomplete");

const watches = new Map();
let followParent = null;
let directConversationId = null;
let chatMode = "auto";
const modeSelect = document.getElementById("chat-mode-select");
const modeButton = document.getElementById("chat-mode-button");
const modeLabel = document.getElementById("chat-mode-label");
const modeList = document.getElementById("chat-mode-list");
const emptyTitle = document.getElementById("chat-empty-title");
const emptyDescription = document.getElementById("chat-empty-description");

let cachedDocs = [];
let autocompleteItems = [];
let autocompleteIndex = -1;

const MODE_INTRO = {
  auto: {
    title: "✨ 智能路由",
    description: "全能自适应：根据输入自动调度快慢通道，识别前缀命令与 @ 实体，智能调用直接问答、知识库、深度研究或项目认知。",
  },
  direct: {
    title: "直接提问",
    description: "直接获得模型回答，不检索本地资料，也不附带引用。",
  },
  rag: {
    title: "知识库问答",
    description: "基于本地资料检索回答，附带片段引用，但不执行完整核验研究。",
  },
  deep: {
    title: "深度研究",
    description: "问题会创建为一次核验研究：结果带引用证据，运行过程实时可见。",
  },
  document: {
    title: "文档研读",
    description: "针对特定文献/PDF进行重点精读与权威笔记提炼。",
  },
  project: {
    title: "项目认知",
    description: "代码架构解构、技术债分析、理论实践映射与健康审计。",
  },
  memory: {
    title: "记忆中心",
    description: "查询用户习惯偏好、修改项目技术规约与管理记忆库。",
  },
};

function closeModeList() {
  modeList.hidden = true;
  modeButton.setAttribute("aria-expanded", "false");
}

function setChatMode(mode) {
  const option = modeList.querySelector(`li[data-mode="${mode}"]`);
  if (!option) return;
  chatMode = mode;
  modeLabel.textContent = option.querySelector("strong").textContent;
  const intro = MODE_INTRO[mode] || MODE_INTRO.auto;
  emptyTitle.textContent = intro.title;
  emptyDescription.textContent = intro.description;
  for (const item of modeList.querySelectorAll("li")) {
    item.setAttribute("aria-selected", item.dataset.mode === mode ? "true" : "false");
  }
  closeModeList();
}

modeButton.addEventListener("click", () => {
  const open = modeList.hidden;
  modeList.hidden = !open;
  modeButton.setAttribute("aria-expanded", open ? "true" : "false");
});

modeList.addEventListener("click", (event) => {
  const item = event.target.closest("li[data-mode]");
  if (item) setChatMode(item.dataset.mode);
});

document.addEventListener("click", (event) => {
  if (!modeSelect.contains(event.target)) closeModeList();
  if (autocomplete && !autocomplete.contains(event.target) && event.target !== input) {
    hideAutocomplete();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!modeList.hidden) closeModeList();
    if (autocomplete && !autocomplete.hidden) hideAutocomplete();
  }
});

function autoGrow(node) {
  node.style.height = "auto";
  node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
}

async function preloadLibraryDocs() {
  try {
    const res = await api("/api/v1/library");
    if (res && res.items) cachedDocs = res.items.slice(0, 30);
  } catch {
    cachedDocs = [];
  }
}

function renderAutocomplete(items) {
  if (!autocomplete) return;
  if (!items || items.length === 0) {
    hideAutocomplete();
    return;
  }
  autocompleteIndex = 0;
  autocomplete.replaceChildren(...items.map((item, idx) => {
    const div = document.createElement("div");
    div.className = `chat-autocomplete-item${idx === 0 ? " active" : ""}`;
    div.dataset.index = String(idx);

    const tag = document.createElement("span");
    tag.className = "chat-autocomplete-tag";
    tag.textContent = item.tag;

    const labelSpan = document.createElement("strong");
    labelSpan.textContent = item.label;

    const descSpan = document.createElement("span");
    descSpan.className = "chat-autocomplete-desc";
    descSpan.textContent = item.desc;

    div.append(tag, labelSpan, descSpan);
    div.addEventListener("click", () => selectAutocompleteItem(item));
    return div;
  }));
  autocomplete.hidden = false;
}

function updateAutocompleteActive() {
  if (!autocomplete) return;
  const nodes = autocomplete.querySelectorAll(".chat-autocomplete-item");
  nodes.forEach((node, idx) => {
    node.classList.toggle("active", idx === autocompleteIndex);
    if (idx === autocompleteIndex) {
      node.scrollIntoView({ block: "nearest" });
    }
  });
}

function hideAutocomplete() {
  if (!autocomplete) return;
  autocomplete.hidden = true;
  autocomplete.replaceChildren();
  autocompleteItems = [];
  autocompleteIndex = -1;
}

function selectAutocompleteItem(item) {
  const val = input.value;
  const pos = input.selectionStart || val.length;
  const beforeCursor = val.slice(0, pos);
  const afterCursor = val.slice(pos);

  if (item.type === "command") {
    const match = beforeCursor.match(/^(\/[a-zA-Z]*)$/);
    if (match) {
      input.value = item.insertText + " " + afterCursor.trimStart();
      const newPos = item.insertText.length + 1;
      input.setSelectionRange(newPos, newPos);
    } else {
      input.value = item.insertText + " " + val;
    }
  } else {
    const match = beforeCursor.match(/(@[\w:-]*)$/);
    if (match) {
      const startIdx = beforeCursor.length - match[0].length;
      const newBefore = val.slice(0, startIdx) + item.insertText + " ";
      input.value = newBefore + afterCursor;
      const newPos = newBefore.length;
      input.setSelectionRange(newPos, newPos);
    }
  }
  hideAutocomplete();
  autoGrow(input);
  input.focus();
}

function handleInputAutocomplete() {
  const val = input.value;
  const pos = input.selectionStart || val.length;
  const beforeCursor = val.slice(0, pos);

  // 1. Prefix Commands
  const cmdMatch = beforeCursor.match(/^\/([a-zA-Z]*)$/);
  if (cmdMatch) {
    const query = cmdMatch[1].toLowerCase();
    const commands = [
      { type: "command", tag: "命令", label: "/deep", desc: "深度研究 · 慢通道学术研讨与证据核验", insertText: "/deep" },
      { type: "command", tag: "命令", label: "/rag", desc: "知识库问答 · 本地语料检索与片段引用", insertText: "/rag" },
      { type: "command", tag: "命令", label: "/doc", desc: "文档研读 · 单篇文献/PDF精读提炼", insertText: "/doc" },
      { type: "command", tag: "命令", label: "/audit", desc: "项目审计 · 代码健康度与架构契约体检", insertText: "/audit" },
      { type: "command", tag: "命令", label: "/mem", desc: "记忆中心 · 查看已生效偏好与约定", insertText: "/mem" },
    ];
    autocompleteItems = commands.filter((c) => c.label.toLowerCase().includes("/" + query));
    renderAutocomplete(autocompleteItems);
    return;
  }

  // 2. @ Entities
  const entityMatch = beforeCursor.match(/@([\w:-]*)$/);
  if (entityMatch) {
    const query = entityMatch[1].toLowerCase();
    const entities = [
      { type: "entity", tag: "项目", label: "@project:current", desc: "当前工程拓扑与架构治理", insertText: "@project:current" },
    ];
    for (const doc of cachedDocs) {
      const docId = doc.document_id || "";
      const docTitle = doc.title || docId;
      entities.push({
        type: "entity",
        tag: "论文",
        label: `@paper:${docId.slice(0, 16)}`,
        desc: docTitle,
        insertText: `@paper:${docId}`,
      });
      entities.push({
        type: "entity",
        tag: "笔记",
        label: `@note:${docId.slice(0, 16)}`,
        desc: `权威笔记: ${docTitle}`,
        insertText: `@note:${docId}`,
      });
    }
    autocompleteItems = entities.filter((e) =>
      e.label.toLowerCase().includes(query) || e.desc.toLowerCase().includes(query)
    ).slice(0, 8);
    renderAutocomplete(autocompleteItems);
    return;
  }

  hideAutocomplete();
}

function scrollThread() {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: reduced ? "auto" : "smooth" });
}

function showChatError(message) {
  errorNode.textContent = message;
  errorNode.hidden = false;
}

function appendUserMessage(text, time, label = "你", parent = thread) {
  const article = document.createElement("article");
  article.className = "chat-msg user";
  article.id = `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const header = document.createElement("header");
  const labelNode = document.createElement("span");
  labelNode.className = "chat-msg-label";
  labelNode.textContent = label;
  const stamp = document.createElement("time");
  stamp.textContent = formatDate(time, true);
  header.append(labelNode, stamp);
  const body = document.createElement("div");
  body.className = "chat-msg-body";
  body.textContent = text;
  article.append(header, body);
  parent.append(article);
  if (parent === thread) refreshTurnNav();
  return article;
}

function normalModeLabel(mode) {
  if (mode === "auto") return "智能路由";
  if (mode === "rag") return "知识库问答";
  if (mode === "deep") return "深度研究";
  if (mode === "document") return "文档研读";
  if (mode === "project") return "项目认知";
  if (mode === "memory") return "记忆中心";
  return "直接问答";
}

function renderConversationList(items, selectedId) {
  historyThreads.replaceChildren(...items.map((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chat-conversation-item${selectedId === item.conversation_id ? " active" : ""}`;
    const title = document.createElement("span");
    title.className = "chat-conversation-title";
    title.textContent = item.title || "新对话";
    title.title = item.last_message_preview || item.title || "新对话";
    const mode = document.createElement("span");
    mode.className = `chat-conversation-mode ${item.active_mode || "direct"}`;
    mode.textContent = normalModeLabel(item.active_mode);
    const count = document.createElement("span");
    count.className = "chat-conversation-count";
    count.textContent = `${item.message_count || 0} 条`;
    button.append(title, mode, count);
    button.addEventListener("click", () => {
      window.location.hash = `#/chat/${encodeURIComponent(item.conversation_id)}`;
      loadConversation(item.conversation_id);
    });
    return button;
  }));
  historyPanel.hidden = !items.length;
}

async function refreshConversationList(selectedId = directConversationId) {
  const conversations = await api("/api/v1/conversations?limit=50").catch(() => ({ items: [] }));
  renderConversationList(conversations.items || [], selectedId);
}

function refreshTurnNav() {
  const turns = [...thread.querySelectorAll(":scope > .chat-msg.user")];
  turnList.replaceChildren();
  turnNav.hidden = turns.length === 0;
  turns.forEach((turn, index) => {
    const link = document.createElement("a");
    link.href = `#${turn.id}`;
    link.className = "chat-turn-link";
    link.dataset.turnTarget = turn.id;
    link.textContent = `${index + 1}. ${(turn.querySelector(".chat-msg-body")?.textContent || "").trim()}`;
    link.title = link.textContent;
    link.addEventListener("click", (event) => { event.preventDefault(); turn.scrollIntoView({ behavior: "smooth", block: "start" }); });
    turnList.append(link);
  });
  if (window.__chatTurnObserver) window.__chatTurnObserver.disconnect();
  window.__chatTurnObserver = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (!visible) return;
    turnList.querySelectorAll(".chat-turn-link").forEach((link) => link.classList.toggle("active", link.dataset.turnTarget === visible.target.id));
  }, { rootMargin: "-15% 0px -70% 0px", threshold: 0 });
  turns.forEach((turn) => window.__chatTurnObserver.observe(turn));
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

function renderCandidateBubble(cand) {
  const bubble = document.createElement("div");
  bubble.className = "memory-candidate-bubble";
  bubble.dataset.candidateId = cand.candidate_id;

  const textWrap = document.createElement("div");
  textWrap.className = "memory-candidate-text";
  const scopeName = cand.scope === "project" ? "项目约定" : "用户偏好";
  textWrap.innerHTML = `<strong>💡 识别到${scopeName}候选：</strong> "${escapeHtml(cand.statement)}"${cand.conflict_with_memory_id ? ' <span style="color:var(--ochre);font-size:11.5px;margin-left:6px;">(潜在语义冲突)</span>' : ''}`;

  const actions = document.createElement("div");
  actions.className = "memory-candidate-actions";

  const approveBtn = document.createElement("button");
  approveBtn.type = "button";
  approveBtn.className = "memory-btn approve";
  approveBtn.textContent = "核准记住";
  approveBtn.addEventListener("click", async () => {
    try {
      approveBtn.disabled = true;
      await api(`/api/v1/memories/candidates/${encodeURIComponent(cand.candidate_id)}/action`, {
        method: "POST",
        body: JSON.stringify({ action: "approve" }),
      });
      showToast("已核准记住并持久化至记忆库");
      textWrap.innerHTML = `<strong>✔ 已核准记住${scopeName}：</strong> "${escapeHtml(cand.statement)}"`;
      actions.remove();
    } catch (e) {
      approveBtn.disabled = false;
      showToast(e.message || "核准失败");
    }
  });

  const rejectBtn = document.createElement("button");
  rejectBtn.type = "button";
  rejectBtn.className = "memory-btn reject";
  rejectBtn.textContent = "忽略";
  rejectBtn.addEventListener("click", async () => {
    try {
      rejectBtn.disabled = true;
      await api(`/api/v1/memories/candidates/${encodeURIComponent(cand.candidate_id)}/action`, {
        method: "POST",
        body: JSON.stringify({ action: "reject" }),
      });
      showToast("已忽略该候选");
      bubble.remove();
    } catch (e) {
      rejectBtn.disabled = false;
      showToast(e.message || "操作失败");
    }
  });

  actions.append(approveBtn, rejectBtn);
  bubble.append(textWrap, actions);
  return bubble;
}

function appendAssistantMessage(content, mode = "direct", routedMode = null, memoryCandidates = []) {
  const article = document.createElement("article");
  const effectiveMode = routedMode || mode;
  article.className = `chat-msg agent direct ${effectiveMode}`;
  const header = document.createElement("header");
  const label = document.createElement("span");
  label.className = "chat-msg-label";
  if (effectiveMode === "rag") {
    label.textContent = "知识库问答 · 本地片段引用";
  } else if (effectiveMode === "deep") {
    label.textContent = "深度研究 · 完整综述报告";
  } else if (effectiveMode === "document") {
    label.textContent = "文档研读 · 单篇文献精读";
  } else if (effectiveMode === "project") {
    label.textContent = "项目认知 · 架构解构";
  } else if (effectiveMode === "memory") {
    label.textContent = "记忆中心 · 偏好与约定";
  } else {
    label.textContent = "直接问答 · 模型知识（未核验）";
  }
  header.append(label);
  if (routedMode) {
    const routeBadge = document.createElement("span");
    routeBadge.className = `chat-route-badge ${routedMode}`;
    routeBadge.textContent = `✨ 路由 · ${normalModeLabel(routedMode)}`;
    header.append(routeBadge);
  }
  const stamp = document.createElement("time");
  stamp.textContent = formatDate(new Date().toISOString(), true);
  header.append(stamp);

  const body = document.createElement("div");
  body.className = "chat-msg-body";
  renderAnswer(body, content || "", "text/markdown");
  article.append(header, body);

  if (memoryCandidates && memoryCandidates.length > 0) {
    for (const cand of memoryCandidates) {
      article.append(renderCandidateBubble(cand));
    }
  }

  thread.append(article);
  return article;
}

function createAgentArticle(runId) {
  const article = document.createElement("article");
  article.className = "chat-msg agent";
  article.dataset.runId = runId;
  const header = document.createElement("header");
  const label = document.createElement("span");
  label.className = "chat-msg-label";
  label.textContent = "研究 Run";
  const time = document.createElement("time");
  header.append(label, time);
  const status = document.createElement("div");
  status.className = "chat-status";
  status.setAttribute("role", "status");
  const body = document.createElement("div");
  body.className = "chat-msg-body";
  const foot = document.createElement("footer");
  foot.className = "chat-msg-foot";
  foot.hidden = true;
  article.append(header, status, body, foot);
  return { article, label, time, status, body, foot };
}

async function fetchAnswerText(detail) {
  const artifactIds = detail.delivery?.artifact_ids || [];
  if (!artifactIds.length) return null;
  const artifacts = await Promise.all(artifactIds.map((artifactId) =>
    api(`/api/v1/runs/${encodeURIComponent(detail.run_id)}/artifacts/${encodeURIComponent(artifactId)}/content`)
  ));
  const report = artifacts.find((item) => item.artifact.media_type.startsWith("text/")) || artifacts[0];
  let content = report.content;
  if (report.artifact.media_type.includes("json")) {
    const parsed = JSON.parse(content);
    content = parsed.answer || parsed.report || JSON.stringify(parsed, null, 2);
  }
  return { content, mediaType: report.artifact.media_type };
}

function detailProgress(detail) {
  const completed = detail.progress?.completed_steps || 0;
  const total = detail.progress?.total_steps || 0;
  const budget = detail.budget || {};
  const tokens = (budget.input_tokens_used || 0) + (budget.output_tokens_used || 0);
  return `第 ${completed} / ${total} 步 · ${tokens.toLocaleString("zh-CN")} tokens`;
}

function renderFinal(agent, detail) {
  agent.status.hidden = true;
  agent.label.textContent = `${familyLabels[detail.task_family] || "研究任务"} · ${detail.run_id}`;
  agent.label.title = detail.run_id;
  agent.time.textContent = formatDate(detail.updated_at, true);

  if (detail.delivery?.disposition === "no_answer") {
    agent.body.textContent = "本次研究没有返回可用答案（NO_ANSWER）。这仍是一次成功的 Run：空结果是明确的交付结论。";
  } else if (detail.state === "failed" && detail.error) {
    agent.body.textContent = "";
  }

  const badge = document.createElement("span");
  badge.className = `mini-state ${detail.state}`;
  badge.textContent = stateLabels[detail.state] || "状态更新";
  const meta = document.createElement("span");
  meta.className = "chat-msg-meta";
  const delivery = detail.delivery;
  if (delivery) {
    const parts = [];
    if (delivery.evidence_ids.length) parts.push(`${delivery.evidence_ids.length} 条证据`);
    if (delivery.limitations.length) parts.push(`${delivery.limitations.length} 条限制`);
    if (delivery.unmet_criteria.length) parts.push(`${delivery.unmet_criteria.length} 条未满足标准`);
    meta.textContent = parts.join(" · ") || "无证据记录";
  } else if (detail.error) {
    meta.textContent = `${detail.error.message}${detail.error.recovery_action ? ` · 建议：${detail.error.recovery_action}` : ""}`;
  } else {
    meta.textContent = detail.status_message;
  }
  const actions = document.createElement("div");
  actions.className = "chat-msg-actions";
  const openRun = document.createElement("button");
  openRun.type = "button";
  openRun.className = "quiet-button";
  openRun.textContent = "在研究中打开";
  openRun.addEventListener("click", () => {
    window.location.hash = `#/research/${encodeURIComponent(detail.run_id)}`;
  });
  actions.append(openRun);
  if (delivery && (VERIFIED_FAMILIES.has(detail.task_family) || detail.task_family === "deep_research")) {
    const follow = document.createElement("button");
    follow.type = "button";
    follow.className = "quiet-button";
    follow.textContent = "继续追问";
    follow.addEventListener("click", () => setFollow(detail.run_id));
    actions.append(follow);
  }
  agent.foot.replaceChildren(badge, meta, actions);
  agent.foot.hidden = false;
}

function makeHandle(agent, detail) {
  const setEvent = (payload) => {
    agent.time.textContent = formatDate(payload.created_at, true);
    agent.status.textContent = payload.message;
  };
  const setDetail = (current) => {
    if (current.state === "needs_attention") {
      agent.status.textContent = "付费外部调用结果未知，需要你的恢复决定——请前往研究视图处理。";
      return;
    }
    agent.status.textContent = `${stateLabels[current.state] || "状态更新"} · ${detailProgress(current)}`;
  };
  return { setEvent, setDetail };
}

/* —— UX-1.1 只读线程视图：按 parent_run_id 追溯到根 Run，不推断、不补全。 —— */

function runQuestion(detail) {
  return detail.research_context?.follow_up_question || detail.query || "未命名研究";
}

function isFollowUp(detail) {
  return Boolean(detail.research_context?.parent_run_id);
}

function buildThreads(details) {
  const nodes = new Map();
  for (const detail of details) {
    nodes.set(detail.run_id, {
      detail,
      parent: detail.research_context?.parent_run_id || null,
    });
  }
  const childrenByParent = new Map();
  const roots = [];
  for (const node of nodes.values()) {
    if (node.parent && nodes.has(node.parent)) {
      const children = childrenByParent.get(node.parent) || [];
      children.push(node);
      childrenByParent.set(node.parent, children);
    } else {
      // 父 Run 不在当前加载窗口：作为截断线程的根呈现，不推断缺失链路。
      node.truncated = Boolean(node.parent);
      roots.push(node);
    }
  }
  const threads = roots.map((root) => {
    const messages = [];
    const seen = new Set();
    const collect = (node) => {
      if (seen.has(node.detail.run_id)) return;
      seen.add(node.detail.run_id);
      messages.push(node);
      for (const child of childrenByParent.get(node.detail.run_id) || []) collect(child);
    };
    collect(root);
    messages.sort((a, b) => (a.detail.created_at < b.detail.created_at ? -1 : 1));
    const latest = messages[messages.length - 1].detail.updated_at || messages[messages.length - 1].detail.created_at;
    return {
      root,
      messages,
      latest,
      truncated: messages.some((item) => item.truncated),
    };
  });
  threads.sort((a, b) => (a.latest < b.latest ? 1 : -1));
  return threads;
}

function renderThreadItem(threadData, expanded) {
  const item = document.createElement("details");
  item.className = "chat-thread-item";
  item.open = expanded;
  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "chat-thread-title";
  title.textContent = runQuestion(threadData.root.detail);
  const meta = document.createElement("span");
  meta.className = "chat-thread-count";
  meta.textContent = `${threadData.messages.length} 问`;
  summary.append(title, meta);
  if (threadData.truncated) {
    const truncated = document.createElement("span");
    truncated.className = "chat-thread-truncated";
    truncated.title = "父 Run 不在当前加载窗口，链路不完整。";
    truncated.textContent = "线程历史不完整";
    summary.append(truncated);
  }
  const body = document.createElement("div");
  body.className = "chat-thread-body";
  for (const node of threadData.messages) {
    renderHistoryExchange(node, body);
  }
  item.append(summary, body);
  return item;
}

function renderHistoryExchange(node, parent) {
  const detail = node.detail;
  appendUserMessage(
    runQuestion(detail),
    detail.created_at,
    isFollowUp(detail) ? "追问" : "你",
    parent
  );
  const agent = createAgentArticle(detail.run_id);
  agent.label.textContent = `${familyLabels[detail.task_family] || "研究任务"} · ${detail.run_id}`;
  agent.label.title = detail.run_id;
  agent.time.textContent = formatDate(detail.updated_at, true);
  const mode = detail.research_context?.mode;
  if (mode && !isFollowUp(detail)) {
    const chip = document.createElement("span");
    chip.className = "chat-msg-mode";
    chip.textContent = modeLabels[mode] || "";
    agent.label.before(chip);
  }
  if (TERMINAL_STATES.has(detail.state)) {
    renderFinalInto(agent, detail);
  } else {
    agent.status.textContent = detail.status_message;
    watchRun(detail.run_id, agent, makeHandle(agent, detail));
  }
  parent.append(agent.article);
}

async function loadHistory() {
  for (const { close } of [...watches.values()]) close();
  watches.clear();
  thread.replaceChildren();
  historyThreads.replaceChildren();
  historyPanel.hidden = true;
  turnNav.hidden = true;
  threadEmpty.hidden = true;
  const loading = document.createElement("div");
  loading.className = "skeleton";
  for (const width of ["46%", "88%", "64%"]) {
    const line = document.createElement("span");
    line.className = "skeleton-line";
    line.style.width = width;
    loading.append(line);
  }
  historyPanel.hidden = false;
  historyThreads.append(loading);
  try {
    const conversations = await api("/api/v1/conversations?limit=50").catch(() => ({ items: [] }));
    const requested = (window.location.hash.match(/^#\/chat\/([^?]+)/) || [])[1];
    const selected = conversations.items?.find((item) => item.conversation_id === decodeURIComponent(requested || "")) || conversations.items?.[0];
    renderConversationList(conversations.items || [], selected?.conversation_id);
    if (selected) await loadConversation(selected.conversation_id);
    if (!selected) { historyPanel.hidden = !(conversations.items || []).length; threadEmpty.hidden = false; }
  } catch (error) {
    historyPanel.hidden = true;
    threadEmpty.hidden = false;
    showToast(error.message);
  } finally {
    loading.remove();
  }
}

async function loadConversation(conversationId) {
  const record = await api(`/api/v1/conversations/${encodeURIComponent(conversationId)}`);
  directConversationId = conversationId;
  if (record.active_mode) setChatMode(record.active_mode);
  thread.replaceChildren();
  for (const message of record.messages || []) {
    if (message.role === "user") appendUserMessage(message.content, message.created_at);
    else appendAssistantMessage(message.content, message.mode, message.mode);
  }
  threadEmpty.hidden = Boolean(record.messages?.length);
  refreshTurnNav();
}

function startNewThread() {
  for (const { close } of [...watches.values()]) close();
  watches.clear();
  directConversationId = null;
  window.location.hash = "#/chat";
  thread.replaceChildren();
  threadEmpty.hidden = false;
  clearFollow();
  setChatMode("auto");
  input.value = "";
  autoGrow(input);
  errorNode.hidden = true;
  hideAutocomplete();
  window.scrollTo({ top: 0, behavior: "auto" });
  input.focus();
}

function closeWatch(runId) {
  watches.get(runId)?.close();
}

function watchRun(runId, agent, handle) {
  closeWatch(runId);
  let cursor = 0;
  let source = null;
  let timer = null;
  let closed = false;
  const seen = new Set();
  const cleanup = () => {
    closed = true;
    if (timer) clearTimeout(timer);
    source?.close();
    watches.delete(runId);
  };
  watches.set(runId, { close: cleanup });
  const open = () => {
    if (closed) return;
    source = new EventSource(`/api/v1/runs/${encodeURIComponent(runId)}/events?after=${cursor}`);
    for (const kind of ["progress", "status", "recovery"]) {
      source.addEventListener(kind, receive);
    }
    source.onerror = async () => {
      source.close();
      if (closed) return;
      // A terminal run makes the backend close SSE intentionally; do not
      // reopen that completed stream as though it were a network failure.
      try {
        const detail = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
        if (TERMINAL_STATES.has(detail.state)) {
          await renderFinalInto(agent, detail);
          cleanup();
          return;
        }
      } catch { /* retry below when the status endpoint is also unavailable */ }
      timer = setTimeout(open, 500);
    };
  };
  const receive = async (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (seen.has(payload.cursor)) return;
    seen.add(payload.cursor);
    cursor = Math.max(cursor, Number(payload.cursor) || 0);
    handle.setEvent(payload);
    try {
      const detail = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
      if (TERMINAL_STATES.has(detail.state)) {
        await renderFinalInto(agent, detail);
        cleanup();
      } else {
        handle.setDetail(detail);
      }
    } catch {
      /* 状态读取失败时保留事件行，等待下一条事件重试。 */
    }
  };
  open();
}

async function renderFinalInto(agent, detail) {
  renderFinal(agent, detail);
  if (detail.delivery) {
    try {
      const answer = await fetchAnswerText(detail);
      if (answer) {
        renderAnswer(agent.body, answer.content, answer.mediaType);
        const conversationId = detail.research_context?.conversation_id;
        if (conversationId) {
          await api(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, {
            method: "POST",
            body: JSON.stringify({ role: "assistant", content: answer.content, run_id: detail.run_id, mode: "deep" }),
          });
          await refreshConversationList(conversationId);
        }
      }
    } catch {
      agent.body.textContent = "交付结果暂时不可读。";
    }
  }
}

function setFollow(runId) {
  followParent = runId;
  document.getElementById("chat-follow-chip").hidden = false;
  document.getElementById("chat-follow-label").textContent = `追问上下文 · ${runId}`;
  modeSelect.hidden = true;
  input.placeholder = "继续追问（将创建一个新的独立 Run，不继承父 Run 结论）";
  autoGrow(input);
  input.focus();
}

function clearFollow() {
  followParent = null;
  document.getElementById("chat-follow-chip").hidden = true;
  modeSelect.hidden = false;
  input.placeholder = DEFAULT_PLACEHOLDER;
}

async function submit() {
  const question = input.value.trim();
  if (!question) {
    showChatError("请输入你的问题。");
    return;
  }
  errorNode.hidden = true;
  sendButton.disabled = true;
  hideAutocomplete();

  try {
    // 1. 追问已有 Run：专属通道
    if (followParent) {
      input.value = "";
      autoGrow(input);
      appendUserMessage(question, new Date().toISOString(), "追问");
      threadEmpty.hidden = true;
      scrollThread();

      const accepted = await api(`/api/v1/runs/${encodeURIComponent(followParent)}/follow-up`, {
        method: "POST",
        body: JSON.stringify({ question }),
      });
      clearFollow();
      const agent = createAgentArticle(accepted.run_id);
      agent.label.textContent = `研究 Run · ${accepted.run_id}`;
      agent.label.title = accepted.run_id;
      agent.status.textContent = "追问任务已保存，正在等待处理。";
      thread.append(agent.article);
      scrollThread();
      watchRun(accepted.run_id, agent, makeHandle(agent, null));
      return;
    }

    // 2. 深度研究专属创建通道（保持与 /api/v1/tasks/deep-research 兼容）
    if (chatMode === "deep") {
      input.value = "";
      autoGrow(input);
      appendUserMessage(question, new Date().toISOString());
      threadEmpty.hidden = true;
      scrollThread();

      if (!directConversationId) directConversationId = `conv-${crypto.randomUUID()}`;
      const accepted = await api("/api/v1/tasks/deep-research", {
        method: "POST",
        body: JSON.stringify({ objective: question, conversation_id: directConversationId }),
      });
      const agent = createAgentArticle(accepted.run_id);
      agent.label.textContent = `研究 Run · ${accepted.run_id}`;
      agent.label.title = accepted.run_id;
      agent.status.textContent = "任务已保存，正在等待处理。";
      thread.append(agent.article);
      scrollThread();
      watchRun(accepted.run_id, agent, makeHandle(agent, null));
      return;
    }

    // 3. 统一全能入口（支持 auto/direct/rag/document/project/memory）
    input.value = "";
    autoGrow(input);
    appendUserMessage(question, new Date().toISOString());
    threadEmpty.hidden = true;
    scrollThread();

    const answer = await api("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({ question, conversation_id: directConversationId, mode: chatMode }),
    });

    directConversationId = answer.conversation_id;
    await refreshConversationList(directConversationId);
    window.history.replaceState(null, "", `#/chat/${encodeURIComponent(directConversationId)}`);

    if (!answer.is_fast_path && answer.run_id) {
      // 慢通道：调度深度核验任务并开启实时观测流
      const agent = createAgentArticle(answer.run_id);
      agent.label.textContent = `深度研究 · ${answer.run_id}`;
      agent.label.title = answer.run_id;
      agent.status.textContent = answer.content || "任务已保存，正在后台执行文献检索与证据核验...";
      thread.append(agent.article);
      scrollThread();
      watchRun(answer.run_id, agent, makeHandle(agent, null));
    } else {
      // 快通道 / 引导问答：附带路由徽标与 HITL 记忆候选气泡
      appendAssistantMessage(answer.content, answer.mode, answer.routed_mode, answer.memory_candidates);
      scrollThread();
    }
  } catch (error) {
    showChatError(error.recoveryAction || error.message);
  } finally {
    sendButton.disabled = false;
  }
}

document.getElementById("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submit();
});

input.addEventListener("keydown", (event) => {
  if (autocomplete && !autocomplete.hidden && autocompleteItems.length > 0) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      autocompleteIndex = (autocompleteIndex + 1) % autocompleteItems.length;
      updateAutocompleteActive();
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      autocompleteIndex = (autocompleteIndex - 1 + autocompleteItems.length) % autocompleteItems.length;
      updateAutocompleteActive();
      return;
    }
    if ((event.key === "Enter" || event.key === "Tab") && autocompleteIndex >= 0) {
      event.preventDefault();
      selectAutocompleteItem(autocompleteItems[autocompleteIndex]);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      hideAutocomplete();
      return;
    }
  }

  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
});

input.addEventListener("input", () => {
  errorNode.hidden = true;
  autoGrow(input);
  handleInputAutocomplete();
});

document.getElementById("chat-clear-follow").addEventListener("click", clearFollow);
document.getElementById("chat-new-thread").addEventListener("click", startNewThread);

registerView("chat", {
  mount: async (conversationId) => {
    preloadLibraryDocs();
    await loadHistory();
    if (conversationId) await loadConversation(conversationId);
  },
  onParam: async (conversationId) => {
    if (conversationId) await loadConversation(conversationId);
  },
  unmount: async () => {
    hideAutocomplete();
    for (const { close } of [...watches.values()]) close();
    watches.clear();
  },
});
