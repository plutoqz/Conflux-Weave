import {
  $,
  api,
  familyLabels,
  formatDate,
  modeLabels,
  renderAnswer,
  showToast,
  stateLabels,
} from "./modules/shared.js?v=v0.3-report-render-4";
import { initRouter, navigate, registerView, replaceHash } from "./modules/router.js";
import "./modules/overview.js";
import "./modules/chat.js";
import "./modules/settings.js";

const state = {
  bootstrapped: false,
  runs: [],
  nextCursor: null,
  selected: null,
  eventSource: null,
  eventRunId: null,
  eventCursor: 0,
  eventReconnectTimer: null,
  eventIds: new Set(),
  evidence: new Map(),
  evidenceList: [],
  inspectorIndex: -1,
  inspectorTrigger: null,
  mutating: false,
};

const INSPECTOR_MEDIA = "(min-width: 1024px)";
const inspectorMedia = window.matchMedia(INSPECTOR_MEDIA);

function makeRunItem(run) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `run-item${state.selected?.run_id === run.run_id ? " selected" : ""}`;
  button.dataset.runId = run.run_id;
  const title = document.createElement("strong");
  title.textContent = run.query || "未命名研究";
  button.title = run.query || "未命名研究";
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
  const latest = state.runs[0];
  $("#hud-activity").textContent = latest ? formatDate(latest.updated_at, true) : "—";
  if (!append && !state.selected && state.runs.length) await selectRun(state.runs[0].run_id);
  if (!state.runs.length && $(".app-shell").dataset.section === "research") {
    $("#empty-view").hidden = false;
    $("#run-view").hidden = true;
  }
}

function flashIfChanged(selector, changed) {
  if (!changed) return;
  const el = $(selector);
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
}

function renderRun(run, prevRun = null) {
  $("#empty-view").hidden = true;
  $("#run-view").hidden = false;
  $("#run-family").textContent = familyLabels[run.task_family] || "研究任务";
  $("#run-date").textContent = formatDate(run.created_at, true);
  $("#run-query").textContent = run.query || "未命名研究";
  $("#run-message").textContent = run.status_message;
  const badge = $("#run-state");
  badge.textContent = stateLabels[run.state] || "状态更新";
  badge.className = `state-badge ${run.state}`;

  const prevBudget = prevRun?.budget || {};
  const completed = run.progress?.completed_steps || 0;
  const total = run.progress?.total_steps || 0;
  $("#progress-value").textContent = `${completed} / ${total}`;
  $("#progress-bar").style.width = `${total ? Math.min(100, completed / total * 100) : 0}%`;
  const budget = run.budget || {};
  const context = run.research_context || {};
  const tokens = (budget.input_tokens_used || 0) + (budget.output_tokens_used || 0);
  const prevTokens = (prevBudget.input_tokens_used || 0) + (prevBudget.output_tokens_used || 0);
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
  $("#hud-corpus").textContent = context.corpus_scope || "未记录";
  $("#hud-corpus").title = context.corpus_scope || "未记录";
  const verified = ["verified_paper_research", "managed_verified_research"].includes(run.task_family);
  $("#confidence-value").textContent = verified && run.state === "complete"
    ? "引用核验完成"
    : verified && run.state === "partial"
      ? "部分核验"
      : run.delivery?.evidence_ids?.length
        ? `${run.delivery.evidence_ids.length} 条证据`
        : "待生成";

  if (prevRun && prevRun.run_id === run.run_id) {
    flashIfChanged("#progress-value", completed !== (prevRun.progress?.completed_steps || 0));
    flashIfChanged("#token-value", tokens !== prevTokens);
    flashIfChanged("#retrieval-value",
      (budget.retrieval_rounds_used || 0) !== (prevBudget.retrieval_rounds_used || 0));
  }

  $("#cancel-run").hidden = run.is_terminal || run.state === "needs_attention" || run.state === "cancelling";
  $("#rerun-run").hidden = !run.is_terminal || run.state === "cancelled";
  $("#follow-up-run").hidden = !run.is_terminal || !verified || !run.delivery;
  $("#retry-run").hidden = run.state !== "needs_attention";
  $("#fail-run").hidden = run.state !== "needs_attention";
  renderBoundaries(run.delivery, run.error);
  renderRuns();
}

const boundaryKinds = {
  限制: "limitation",
  未满足: "unmet",
  后续动作: "action",
  错误: "error",
  恢复动作: "recovery",
};

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
    row.dataset.kind = boundaryKinds[label] || "limitation";
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

async function loadEvidence(runId, evidenceIds) {
  const list = $("#evidence-list");
  // 集合未变（典型：SSE 历史重放触发的重复 loadDelivery）：
  // 跳过重建，保留打开的 Inspector 与现有 DOM。
  const currentIds = state.evidenceList.map((item) => item.evidence_id);
  const unchanged = currentIds.length === evidenceIds.length
    && evidenceIds.every((id, index) => id === currentIds[index]);
  if (unchanged) {
    $("#evidence-count").textContent = state.evidenceList.length ? `${state.evidenceList.length} 条` : "";
    $("#evidence-section").hidden = !state.evidenceList.length;
    return;
  }
  state.evidence.clear();
  state.evidenceList = [];
  state.inspectorIndex = -1;
  closeInspector(false);
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
  state.evidenceList = available;
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
    source.title = item.source_snapshot_id;
    button.append(title, quote, source);
    button.addEventListener("click", () => openEvidence(item, index, button));
    return button;
  }));
  $("#evidence-count").textContent = `${available.length} 条`;
  $("#evidence-section").hidden = !available.length;
}

function openEvidence(item, index, trigger) {
  if (inspectorMedia.matches) {
    openInspector(index, trigger);
    return;
  }
  $("#evidence-title").textContent = `证据 ${index + 1}`;
  $("#evidence-quote").textContent = item.quote;
  $("#evidence-source").textContent = item.source_snapshot_id;
  $("#evidence-method").textContent = item.extraction_method;
  $("#evidence-locator").textContent = JSON.stringify(item.locator, null, 2);
  $("#evidence-dialog").showModal();
}

function openInspector(index, trigger = state.inspectorTrigger) {
  state.inspectorIndex = index;
  state.inspectorTrigger = trigger;
  const item = state.evidenceList[index];
  if (!item) return;
  $("#insp-title").textContent = `证据 ${index + 1}`;
  $("#insp-quote").textContent = item.quote;
  $("#insp-source").textContent = item.source_snapshot_id;
  $("#insp-method").textContent = item.extraction_method;
  $("#insp-locator").textContent = JSON.stringify(item.locator, null, 2);
  $("#insp-position").textContent = `${index + 1} / ${state.evidenceList.length}`;
  $("#insp-prev").disabled = index <= 0;
  $("#insp-next").disabled = index >= state.evidenceList.length - 1;
  $("#evidence-inspector").hidden = false;
  $(".app-shell").dataset.inspector = "open";
  $("#close-inspector").focus();
}

function closeInspector(restoreFocus = true) {
  const inspector = $("#evidence-inspector");
  if (!inspector || inspector.hidden) return;
  const trigger = state.inspectorTrigger;
  inspector.hidden = true;
  $(".app-shell").dataset.inspector = "closed";
  state.inspectorIndex = -1;
  state.inspectorTrigger = null;
  if (restoreFocus && trigger?.isConnected) trigger.focus();
}

function stepInspector(delta) {
  const next = state.inspectorIndex + delta;
  if (next < 0 || next >= state.evidenceList.length) return;
  openInspector(next);
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
    source.onerror = async () => {
      source.close();
      if (state.eventRunId !== run.run_id || state.selected?.is_terminal) return;
      // The server closes SSE normally after the terminal event. Check state
      // before treating that EOF as a transient network failure.
      try {
        const current = await api(`/api/v1/runs/${encodeURIComponent(run.run_id)}`);
        if (state.eventRunId !== run.run_id || current.is_terminal) {
          if (current.is_terminal && state.selected?.run_id === run.run_id) {
            const previous = state.selected;
            state.selected = current;
            renderRun(current, previous);
            await loadDelivery(current);
          }
          return;
        }
      } catch { /* retry below when the status endpoint is also unavailable */ }
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
    item.className = "event-item enter";
    item.addEventListener("animationend", () => item.classList.remove("enter"), { once: true });
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
      if (state.eventRunId !== payload.run_id || state.selected?.run_id !== payload.run_id) return;
      const prev = state.selected;
      state.selected = current;
      renderRun(current, prev);
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
    replaceHash(`#/research/${encodeURIComponent(runId)}`);
  } catch (error) { showToast(error.message); }
}

async function mutateRun(decision = null) {
  if (!state.selected || state.mutating) return;
  state.mutating = true;
  const action = decision === "cancel" ? "cancel" : "resume";
  const body = decision && decision !== "cancel" ? JSON.stringify({ decision }) : undefined;
  const buttons = ["#cancel-run", "#retry-run", "#fail-run"].map((selector) => $(selector));
  buttons.forEach((button) => { if (button) button.disabled = true; });
  try {
    const run = await api(`/api/v1/runs/${encodeURIComponent(state.selected.run_id)}/${action}`, {
      method: "POST", body,
    });
    const prev = state.selected;
    state.selected = run;
    renderRun(run, prev);
    await loadRuns();
    connectEvents(run);
  } catch (error) { showToast(error.recoveryAction || error.message); }
  finally {
    buttons.forEach((button) => { if (button) button.disabled = false; });
    state.mutating = false;
  }
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
    if ($(".app-shell").dataset.section !== "research") {
      navigate(`#/research/${encodeURIComponent(accepted.run_id)}`);
    }
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
  $("#answer-tab").tabIndex = answer ? 0 : -1;
  $("#activity-tab").setAttribute("aria-selected", String(!answer));
  $("#activity-tab").tabIndex = answer ? -1 : 0;
  $("#answer-panel").hidden = !answer;
  $("#activity-panel").hidden = answer;
}

function handleTabKeydown(event) {
  const tabs = [$("#answer-tab"), $("#activity-tab")];
  const current = tabs.indexOf(event.currentTarget);
  let target = null;
  if (event.key === "ArrowRight") target = tabs[(current + 1) % tabs.length];
  if (event.key === "ArrowLeft") target = tabs[(current - 1 + tabs.length) % tabs.length];
  if (event.key === "Home") target = tabs[0];
  if (event.key === "End") target = tabs[tabs.length - 1];
  if (!target) return;
  event.preventDefault();
  setTab(target.id === "answer-tab" ? "answer" : "activity");
  target.focus();
}

async function checkHealth() {
  const node = $("#health");
  try {
    const health = await api("/api/v1/health/ready");
    node.className = `health ${health.status === "ready" ? "ready" : "not-ready"}`;
    node.lastElementChild.textContent = health.status === "ready" ? "服务就绪" : "配置待完善";
    $("#hud-provider").textContent = health.status === "ready" ? "就绪" : "配置待完善";
  } catch {
    node.className = "health not-ready";
    node.lastElementChild.textContent = "服务不可用";
    $("#hud-provider").textContent = "不可用";
  }
}

function toggleSidebar() {
  const shell = $(".app-shell");
  const narrow = shell.dataset.sidebar === "narrow";
  shell.dataset.sidebar = narrow ? "full" : "narrow";
  $("#toggle-sidebar").setAttribute("aria-expanded", String(narrow));
}

function toggleHud() {
  const shell = $(".app-shell");
  const expanded = shell.dataset.hud === "expanded";
  shell.dataset.hud = expanded ? "collapsed" : "expanded";
  $("#hud-toggle").setAttribute("aria-expanded", String(!expanded));
  $("#hud-body").hidden = expanded;
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
document.querySelectorAll("[data-open-task]").forEach((button) => {
  button.addEventListener("click", openTaskDialog);
});
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
$("#answer-tab").addEventListener("keydown", handleTabKeydown);
$("#activity-tab").addEventListener("keydown", handleTabKeydown);
$("[data-close-evidence]").addEventListener("click", () => $("#evidence-dialog").close());
$("#toggle-sidebar").addEventListener("click", toggleSidebar);
$("#hud-toggle").addEventListener("click", toggleHud);
$("#close-inspector").addEventListener("click", closeInspector);
$("#insp-prev").addEventListener("click", () => stepInspector(-1));
$("#insp-next").addEventListener("click", () => stepInspector(1));
inspectorMedia.addEventListener("change", (event) => {
  if (!event.matches) closeInspector(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#evidence-inspector").hidden) closeInspector();
});
document.addEventListener("click", (event) => {
  const citation = event.target.closest(".citation-link");
  if (!citation) return;
  const index = Number(citation.dataset.citationIndex) - 1;
  if (!Number.isInteger(index) || !state.evidenceList[index]) return;
  event.preventDefault();
  openEvidence(state.evidenceList[index], index, citation);
});
$("#task-form").addEventListener("submit", (event) => {
  if (event.submitter?.value !== "cancel") event.preventDefault();
});
$("#follow-up-form").addEventListener("submit", (event) => {
  if (event.submitter?.value !== "cancel") event.preventDefault();
});

async function mountResearch(param) {
  if (!state.bootstrapped) {
    state.bootstrapped = true;
    if (param) await selectRun(param);
    await loadRuns();
  } else if (state.selected) {
    await selectRun(state.selected.run_id);
  } else {
    await loadRuns();
  }
  if (param && param !== state.selected?.run_id) await selectRun(param);
}

registerView("research", {
  mount: mountResearch,
  onParam: async (param) => {
    if (param && param !== state.selected?.run_id) await selectRun(param);
  },
  unmount: async () => {
    closeEvents();
    closeInspector(false);
  },
});

checkHealth().catch((error) => showToast(error.message));
initRouter();
