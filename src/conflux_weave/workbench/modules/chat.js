/* 统一对话入口（v1.1 线程化）：提问即创建核验研究 Run，事件流与交付以对话形式呈现。
   历史按 parent_run_id 追溯为只读线程（UX-1.1）：不删除、不改审计语义；
   "新对话"仅清空当前前端视图。P4 统一对话路由就绪后仅替换提交入口，UI 合同不变。 */

import { api, familyLabels, formatDate, modeLabels, renderAnswer, showToast, stateLabels } from "./shared.js";
import { registerView } from "./router.js";

const TERMINAL_STATES = new Set(["complete", "partial", "failed", "cancelled", "expired"]);
const VERIFIED_FAMILIES = new Set(["verified_paper_research", "managed_verified_research"]);
const HISTORY_RUN_LIMIT = 20;
const HISTORY_DETAIL_LIMIT = 12;
const DEFAULT_PLACEHOLDER = "输入你的研究问题，Enter 发送，Shift+Enter 换行";

const thread = document.getElementById("chat-thread");
const threadEmpty = document.getElementById("chat-thread-empty");
const historyPanel = document.getElementById("chat-history");
const historyThreads = document.getElementById("chat-history-threads");
const input = document.getElementById("chat-input");
const errorNode = document.getElementById("chat-error");
const sendButton = document.getElementById("chat-send");

const watches = new Map();
let followParent = null;
let directConversationId = null;
let chatMode = "direct";
const modeSelect = document.getElementById("chat-mode-select");
const modeButton = document.getElementById("chat-mode-button");
const modeLabel = document.getElementById("chat-mode-label");
const modeList = document.getElementById("chat-mode-list");

function closeModeList() {
  modeList.hidden = true;
  modeButton.setAttribute("aria-expanded", "false");
}

function setChatMode(mode) {
  const option = modeList.querySelector(`li[data-mode="${mode}"]`);
  if (!option) return;
  chatMode = mode;
  modeLabel.textContent = option.querySelector("strong").textContent;
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
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !modeList.hidden) closeModeList();
});

function autoGrow(node) {
  node.style.height = "auto";
  node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
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
  return article;
}

function appendAssistantMessage(content, mode = "direct") {
  const article = document.createElement("article");
  article.className = `chat-msg agent direct ${mode}`;
  const header = document.createElement("header");
  const label = document.createElement("span");
  label.className = "chat-msg-label";
  label.textContent = mode === "rag" ? "知识库问答 · 未核验聚合" : "直接问答 · 模型知识（未核验）";
  const stamp = document.createElement("time");
  stamp.textContent = formatDate(new Date().toISOString(), true);
  header.append(label, stamp);
  const body = document.createElement("div");
  body.className = "chat-msg-body";
  renderAnswer(body, content || "", "text/markdown");
  article.append(header, body);
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
  if (delivery && VERIFIED_FAMILIES.has(detail.task_family)) {
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
    const page = await api(`/api/v1/runs?limit=${HISTORY_RUN_LIMIT}`);
    const candidates = (page.items || [])
      .filter((item) => VERIFIED_FAMILIES.has(item.task_family))
      .slice(0, HISTORY_DETAIL_LIMIT);
    const details = [];
    for (const item of candidates) {
      try {
        details.push(await api(`/api/v1/runs/${encodeURIComponent(item.run_id)}`));
      } catch { continue; }
    }
    const chatPage = await api("/api/v1/chat/messages?limit=20").catch(() => ({ items: [] }));
    for (const message of chatPage.items || []) {
      if (message.role === "user") {
        appendUserMessage(message.content, message.created_at);
      } else {
        appendAssistantMessage(message.content, message.mode);
      }
    }
    if ((chatPage.items || []).length) threadEmpty.hidden = true;
    const threads = buildThreads(details);
    if (!threads.length) {
      if (!(chatPage.items || []).length) {
        historyPanel.hidden = true;
        threadEmpty.hidden = false;
      }
      return;
    }
    historyThreads.replaceChildren(
      ...threads.map((threadData, index) => renderThreadItem(threadData, index === 0))
    );
    historyPanel.hidden = false;
    threadEmpty.hidden = true;
  } catch (error) {
    historyPanel.hidden = true;
    threadEmpty.hidden = false;
    showToast(error.message);
  } finally {
    loading.remove();
  }
}

function startNewThread() {
  for (const { close } of [...watches.values()]) close();
  watches.clear();
  directConversationId = null;
  thread.replaceChildren();
  threadEmpty.hidden = false;
  clearFollow();
  input.value = "";
  autoGrow(input);
  errorNode.hidden = true;
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
    source.onerror = () => {
      source.close();
      if (closed) return;
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
      if (answer) renderAnswer(agent.body, answer.content, answer.mediaType);
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
  const mode = chatMode;
  try {
    if (mode === "direct" || mode === "rag") {
      input.value = "";
      autoGrow(input);
      appendUserMessage(question, new Date().toISOString());
      threadEmpty.hidden = true;
      scrollThread();
      const answer = await api("/api/v1/chat", {
        method: "POST",
        body: JSON.stringify({ question, conversation_id: directConversationId, mode }),
      });
      directConversationId = answer.conversation_id;
      appendAssistantMessage(answer.content, answer.mode);
      scrollThread();
      return;
    }
    let accepted;
    if (followParent) {
      accepted = await api(`/api/v1/runs/${encodeURIComponent(followParent)}/follow-up`, {
        method: "POST",
        body: JSON.stringify({ question }),
      });
      clearFollow();
    } else {
      accepted = await api("/api/v1/tasks/deep-research", {
        method: "POST",
        body: JSON.stringify({ objective: question }),
      });
    }
    input.value = "";
    autoGrow(input);
    appendUserMessage(question, new Date().toISOString());
    const agent = createAgentArticle(accepted.run_id);
    agent.label.textContent = `研究 Run · ${accepted.run_id}`;
    agent.label.title = accepted.run_id;
    agent.status.textContent = "任务已保存，正在等待处理。";
    thread.append(agent.article);
    threadEmpty.hidden = true;
    scrollThread();
    watchRun(accepted.run_id, agent, makeHandle(agent, null));
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
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
});
input.addEventListener("input", () => {
  errorNode.hidden = true;
  autoGrow(input);
});
document.getElementById("chat-clear-follow").addEventListener("click", clearFollow);
document.getElementById("chat-new-thread").addEventListener("click", startNewThread);

registerView("chat", {
  mount: loadHistory,
  unmount: async () => {
    for (const { close } of [...watches.values()]) close();
    watches.clear();
  },
});
