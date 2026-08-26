const $ = (selector) => document.querySelector(selector);

const state = {
  runs: [],
  nextCursor: null,
  selected: null,
  eventSource: null,
  eventRunId: null,
  eventCursor: 0,
  eventReconnectTimer: null,
  eventIds: new Set(),
  evidence: new Map(),
};

const stateLabels = {
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

const familyLabels = {
  paper_discovery: "论文发现",
  research_fixture: "离线研究验证",
  verified_paper_research: "核验研究",
  managed_verified_research: "Manager 研究",
};

const modeLabels = {
  discovery: "论文获取",
  single: "单 Agent",
  managed: "Manager 协作",
  fixture: "离线验证",
};

async function api(path, options = {}) {
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

function formatDate(value, detailed = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", detailed
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { month: "numeric", day: "numeric" }).format(date);
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 4200);
}

function makeRunItem(run) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `run-item${state.selected?.run_id === run.run_id ? " selected" : ""}`;
  button.dataset.runId = run.run_id;
  const title = document.createElement("strong");
  title.textContent = run.query || "未命名研究";
  const meta = document.createElement("span");
  meta.className = "item-meta";
  const runState = document.createElement("span");
  runState.className = `mini-state ${run.state}`;
  runState.textContent = stateLabels[run.state] || "状态更新";
  const date = document.createElement("span");
  date.textContent = formatDate(run.updated_at);
  meta.append(runState, date);
  button.append(title, meta);
  button.addEventListener("click", () => selectRun(run.run_id));
  return button;
}

function renderRuns() {
  const list = $("#run-list");
  list.replaceChildren(...state.runs.map(makeRunItem));
  $("#load-more").hidden = !state.nextCursor;
}

async function loadRuns({ append = false } = {}) {
  const cursor = append && state.nextCursor ? `&cursor=${encodeURIComponent(state.nextCursor)}` : "";
  const page = await api(`/api/v1/runs?limit=20${cursor}`);
  state.runs = append ? [...state.runs, ...page.items] : page.items;
  state.nextCursor = page.next_cursor;
  renderRuns();
  if (!append && !state.selected && state.runs.length) await selectRun(state.runs[0].run_id);
  if (!state.runs.length) {
    $("#empty-view").hidden = false;
    $("#run-view").hidden = true;
  }
}

function renderRun(run) {
  $("#empty-view").hidden = true;
  $("#run-view").hidden = false;
  $("#run-family").textContent = familyLabels[run.task_family] || "研究任务";
  $("#run-date").textContent = formatDate(run.created_at, true);
  $("#run-query").textContent = run.query || "未命名研究";
  $("#run-message").textContent = run.status_message;
  const badge = $("#run-state");
  badge.textContent = stateLabels[run.state] || "状态更新";
  badge.className = `state-badge ${run.state}`;

  const completed = run.progress?.completed_steps || 0;
  const total = run.progress?.total_steps || 0;
  $("#progress-value").textContent = `${completed} / ${total}`;
  $("#progress-bar").style.width = `${total ? Math.min(100, completed / total * 100) : 0}%`;
  const budget = run.budget || {};
  const context = run.research_context || {};
  const tokens = (budget.input_tokens_used || 0) + (budget.output_tokens_used || 0);
  const tokenLimit = (budget.input_tokens_limit || 0) + (budget.output_tokens_limit || 0);
  $("#token-value").textContent = `${tokens.toLocaleString("zh-CN")} tokens`;
  $("#budget-state").textContent = tokenLimit ? `上限 ${tokenLimit.toLocaleString("zh-CN")}` : "未记录上限";
  $("#retrieval-value").textContent = `${budget.retrieval_rounds_used || 0} / ${budget.retrieval_rounds_limit || 0}`;
  $("#tool-value").textContent = `工具调用 ${budget.tool_calls_used || 0} / ${budget.tool_calls_limit || 0}`;
  $("#cost-value").textContent = budget.estimated_cost_limit && budget.estimated_cost_limit !== "unavailable"
    ? budget.estimated_cost_limit : "未提供";
  $("#cost-state").textContent = budget.cost_enforcement === "unavailable" ? "未启用金额强制" : budget.cost_enforcement;
  $("#run-mode").textContent = modeLabels[context.mode] || "研究任务";
  $("#corpus-scope").textContent = context.corpus_scope || "未记录";
  const verified = ["verified_paper_research", "managed_verified_research"].includes(run.task_family);
  $("#confidence-value").textContent = verified && run.state === "complete"
    ? "引用核验完成"
    : verified && run.state === "partial"
      ? "部分核验"
      : run.delivery?.evidence_ids?.length
        ? `${run.delivery.evidence_ids.length} 条证据`
        : "待生成";

  $("#cancel-run").hidden = run.is_terminal || run.state === "needs_attention" || run.state === "cancelling";
  $("#rerun-run").hidden = !run.is_terminal || run.state === "cancelled";
  $("#follow-up-run").hidden = !run.is_terminal || !verified || !run.delivery;
  $("#retry-run").hidden = run.state !== "needs_attention";
  $("#fail-run").hidden = run.state !== "needs_attention";
  renderBoundaries(run.delivery, run.error);
  renderRuns();
}

function renderBoundaries(delivery, error) {
  const section = $("#limitations-section");
  const list = $("#limitations-list");
  const items = [];
  for (const value of delivery?.limitations || []) items.push(["限制", value]);
  for (const value of delivery?.unmet_criteria || []) items.push(["未满足", value]);
  for (const value of delivery?.recovery_actions || []) items.push(["后续动作", value]);
  if (error?.message) items.push(["错误", error.message]);
  if (error?.recovery_action) items.push(["恢复动作", error.recovery_action]);
  list.replaceChildren(...items.map(([label, value]) => {
    const row = document.createElement("div");
    row.className = "boundary-item";
    row.textContent = `${label}：${value}`;
    return row;
  }));
  section.hidden = !items.length;
}

async function loadDelivery(run) {
  $("#answer-loading").hidden = false;
  $("#answer-content").hidden = true;
  $("#answer-empty").hidden = true;
  const evidenceIds = run.delivery?.evidence_ids || [];
  await loadEvidence(run.run_id, evidenceIds);
  const artifactIds = run.delivery?.artifact_ids || [];
  if (!artifactIds.length) {
    $("#answer-loading").hidden = true;
    $("#answer-empty").hidden = false;
    return;
  }
  try {
    const artifacts = await Promise.all(artifactIds.map((artifactId) =>
      api(`/api/v1/runs/${encodeURIComponent(run.run_id)}/artifacts/${encodeURIComponent(artifactId)}/content`)
    ));
    const report = artifacts.find((item) => item.artifact.media_type.startsWith("text/")) || artifacts[0];
    let content = report.content;
    if (report.artifact.media_type.includes("json")) {
      const parsed = JSON.parse(content);
      content = parsed.answer || parsed.report || JSON.stringify(parsed, null, 2);
    }
    const answer = $("#answer-content");
    renderAnswer(answer, content, report.artifact.media_type);
    answer.hidden = false;
  } catch (error) {
    $("#answer-empty").hidden = false;
    $("#answer-empty strong").textContent = "交付结果暂时不可读";
    $("#answer-empty span").textContent = error.message;
  } finally {
    $("#answer-loading").hidden = true;
  }
}

function renderAnswer(node, content, mediaType) {
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

async function loadEvidence(runId, evidenceIds) {
  const list = $("#evidence-list");
  state.evidence.clear();
  if (!evidenceIds.length) {
    $("#evidence-section").hidden = true;
    list.replaceChildren();
    return;
  }
  const items = await Promise.all(evidenceIds.map(async (evidenceId) => {
    try {
      const item = await api(`/api/v1/evidence/${encodeURIComponent(evidenceId)}?run_id=${encodeURIComponent(runId)}`);
      state.evidence.set(evidenceId, item);
      return item;
    } catch { return null; }
  }));
  const available = items.filter(Boolean);
  list.replaceChildren(...available.map((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "evidence-item";
    const title = document.createElement("strong");
    title.textContent = `证据 ${index + 1}`;
    const quote = document.createElement("span");
    quote.textContent = item.quote;
    const source = document.createElement("small");
    const page = item.locator?.page ? ` · p.${item.locator.page}` : "";
    source.textContent = `${item.source_snapshot_id}${page}`;
    button.append(title, quote, source);
    button.addEventListener("click", () => openEvidence(item, index + 1));
    return button;
  }));
  $("#evidence-count").textContent = `${available.length} 条`;
  $("#evidence-section").hidden = !available.length;
}

function openEvidence(item, index) {
  $("#evidence-title").textContent = `证据 ${index}`;
  $("#evidence-quote").textContent = item.quote;
  $("#evidence-source").textContent = item.source_snapshot_id;
  $("#evidence-method").textContent = item.extraction_method;
  $("#evidence-locator").textContent = JSON.stringify(item.locator, null, 2);
  $("#evidence-dialog").showModal();
}

function closeEvents() {
  if (state.eventReconnectTimer) {
    clearTimeout(state.eventReconnectTimer);
    state.eventReconnectTimer = null;
  }
  state.eventSource?.close();
  state.eventSource = null;
  state.eventRunId = null;
}

function connectEvents(run) {
  closeEvents();
  state.eventIds.clear();
  state.eventRunId = run.run_id;
  state.eventCursor = 0;
  $("#event-list").replaceChildren();
  $("#event-count").textContent = "0";
  $("#event-empty").hidden = false;
  const open = () => {
    if (state.eventRunId !== run.run_id) return;
    const source = new EventSource(`/api/v1/runs/${encodeURIComponent(run.run_id)}/events?after=${state.eventCursor}`);
    state.eventSource = source;
    source.onerror = () => {
      source.close();
      if (state.eventRunId !== run.run_id || state.selected?.is_terminal) return;
      state.eventReconnectTimer = setTimeout(() => {
        state.eventReconnectTimer = null;
        open();
      }, 500);
    };
    for (const kind of ["progress", "status", "recovery"]) source.addEventListener(kind, receive);
  };
  const receive = async (event) => {
    const payload = JSON.parse(event.data);
    if (state.selected?.run_id !== payload.run_id || state.eventIds.has(payload.cursor)) return;
    state.eventIds.add(payload.cursor);
    state.eventCursor = Math.max(state.eventCursor, Number(payload.cursor) || 0);
    const item = document.createElement("div");
    item.className = "event-item";
    const message = document.createElement("strong");
    message.textContent = payload.message;
    const time = document.createElement("time");
    time.textContent = formatDate(payload.created_at, true);
    item.append(message, time);
    $("#event-list").append(item);
    $("#event-empty").hidden = true;
    $("#event-count").textContent = String(state.eventIds.size);
    try {
      const current = await api(`/api/v1/runs/${encodeURIComponent(payload.run_id)}`);
      state.selected = current;
      renderRun(current);
      await loadDelivery(current);
      if (current.is_terminal) {
        await loadRuns();
      }
    } catch (error) { showToast(error.message); }
  };
  open();
}

async function selectRun(runId) {
  closeEvents();
  try {
    const run = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
    state.selected = run;
    renderRun(run);
    await loadDelivery(run);
    connectEvents(run);
  } catch (error) { showToast(error.message); }
}

async function mutateRun(decision = null) {
  if (!state.selected) return;
  const action = decision === "cancel" ? "cancel" : "resume";
  const body = decision && decision !== "cancel" ? JSON.stringify({ decision }) : undefined;
  try {
    const run = await api(`/api/v1/runs/${encodeURIComponent(state.selected.run_id)}/${action}`, {
      method: "POST", body,
    });
    state.selected = run;
    renderRun(run);
    await loadRuns();
    connectEvents(run);
  } catch (error) { showToast(error.recoveryAction || error.message); }
}

async function rerunSelected() {
  if (!state.selected) return;
  const button = $("#rerun-run");
  button.disabled = true;
  try {
    const accepted = await api(`/api/v1/runs/${encodeURIComponent(state.selected.run_id)}/rerun`, { method: "POST" });
    await loadRuns();
    await selectRun(accepted.run_id);
  } catch (error) {
    showToast(error.recoveryAction || error.message);
  } finally {
    button.disabled = false;
  }
}

function openFollowUpDialog() {
  if (!state.selected) return;
  $("#follow-up-question").value = "";
  $("#follow-up-error").hidden = true;
  $("#follow-up-dialog").showModal();
  $("#follow-up-question").focus();
}

async function submitFollowUp() {
  if (!state.selected) return;
  const question = $("#follow-up-question").value.trim();
  const errorNode = $("#follow-up-error");
  if (!question) {
    errorNode.textContent = "请输入追问。";
    errorNode.hidden = false;
    return;
  }
  const button = $("#submit-follow-up");
  button.disabled = true;
  try {
    const accepted = await api(`/api/v1/runs/${encodeURIComponent(state.selected.run_id)}/follow-up`, {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    $("#follow-up-dialog").close();
    await loadRuns();
    await selectRun(accepted.run_id);
  } catch (error) {
    errorNode.textContent = error.recoveryAction || error.message;
    errorNode.hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function submitTask() {
  const query = $("#query").value.trim();
  const mode = document.querySelector('input[name="task_mode"]:checked')?.value || "single";
  const topics = $("#topics").value.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
  const errorNode = $("#task-error");
  if (!query) {
    errorNode.textContent = "请输入研究问题。";
    errorNode.hidden = false;
    return;
  }
  const button = $("#submit-task");
  button.disabled = true;
  button.textContent = "正在创建...";
  try {
    const fixture = mode === "fixture";
    const discovery = mode === "discovery";
    const endpoint = fixture
      ? "/api/v1/tasks/research-fixture"
      : discovery
        ? "/api/v1/tasks/research"
        : "/api/v1/tasks/verified-research";
    const accepted = await api(endpoint, {
      method: "POST",
      body: JSON.stringify(fixture
        ? { objective: query }
        : discovery
          ? { query, topics, max_results: Number($("#max-results").value) }
          : { objective: query, mode, max_subquestions: Number($("#max-subquestions").value) }),
    });
    $("#task-dialog").close();
    $("#task-form").reset();
    $("#max-results").value = "15";
    $("#max-subquestions").value = "4";
    updateTaskMode();
    errorNode.hidden = true;
    await loadRuns();
    await selectRun(accepted.run_id);
  } catch (error) {
    errorNode.textContent = error.recoveryAction || error.message;
    errorNode.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = "创建 Run";
  }
}

function setTab(name) {
  const answer = name === "answer";
  $("#answer-tab").setAttribute("aria-selected", String(answer));
  $("#activity-tab").setAttribute("aria-selected", String(!answer));
  $("#answer-panel").hidden = !answer;
  $("#activity-panel").hidden = answer;
}

async function checkHealth() {
  const node = $("#health");
  try {
    const health = await api("/api/v1/health/ready");
    node.className = `health ${health.status === "ready" ? "ready" : "not-ready"}`;
    node.lastElementChild.textContent = health.status === "ready" ? "服务就绪" : "配置待完善";
  } catch {
    node.className = "health not-ready";
    node.lastElementChild.textContent = "服务不可用";
  }
}

function openTaskDialog() {
  $("#task-error").hidden = true;
  updateTaskMode();
  $("#task-dialog").showModal();
  $("#query").focus();
}

function updateTaskMode() {
  const mode = document.querySelector('input[name="task_mode"]:checked')?.value || "single";
  const fixture = mode === "fixture";
  const discovery = mode === "discovery";
  const managed = mode === "managed";
  $("#query-label").textContent = fixture ? "验证目标" : "研究问题";
  $("#discovery-options").hidden = !discovery;
  $("#managed-options").hidden = !managed;
}

$("#new-task").addEventListener("click", openTaskDialog);
$("[data-open-task]").addEventListener("click", openTaskDialog);
$("#load-more").addEventListener("click", () => loadRuns({ append: true }));
$("#submit-task").addEventListener("click", submitTask);
document.querySelectorAll('input[name="task_mode"]').forEach((input) => {
  input.addEventListener("change", updateTaskMode);
});
$("#cancel-run").addEventListener("click", () => mutateRun("cancel"));
$("#refresh-run").addEventListener("click", () => state.selected && selectRun(state.selected.run_id));
$("#rerun-run").addEventListener("click", rerunSelected);
$("#follow-up-run").addEventListener("click", openFollowUpDialog);
$("#submit-follow-up").addEventListener("click", submitFollowUp);
$("#retry-run").addEventListener("click", () => mutateRun("retry_unknown_external"));
$("#fail-run").addEventListener("click", () => mutateRun("fail_unknown_external"));
$("#answer-tab").addEventListener("click", () => setTab("answer"));
$("#activity-tab").addEventListener("click", () => setTab("activity"));
$("[data-close-evidence]").addEventListener("click", () => $("#evidence-dialog").close());
$("#task-form").addEventListener("submit", (event) => {
  if (event.submitter?.value !== "cancel") event.preventDefault();
});
$("#follow-up-form").addEventListener("submit", (event) => {
  if (event.submitter?.value !== "cancel") event.preventDefault();
});

await Promise.all([checkHealth(), loadRuns()]).catch((error) => showToast(error.message));
