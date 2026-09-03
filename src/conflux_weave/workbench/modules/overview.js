/* 总览工作台：快速开始、真实系统状态与最近研究。所有数字均来自后端。 */

import { api, formatDate, renderChecks, stateLabels } from "./shared.js?v=v0.3-report-render-4";
import { registerView } from "./router.js";

async function renderHealth() {
  const checksNode = document.getElementById("overview-checks");
  const alert = document.getElementById("overview-alert");
  const alertText = document.getElementById("overview-alert-text");
  const corpusNode = document.getElementById("overview-corpus");
  try {
    const health = await api("/api/v1/health/ready");
    renderChecks(checksNode, health.checks || []);
    const providerCheck = (health.checks || []).find((item) => item.name === "provider");
    if (health.status !== "ready") {
      const problem = (health.checks || []).find((item) => item.status !== "ready");
      alertText.textContent = problem
        ? `${providerCheck && problem.name === "provider" ? "模型服务尚未配置" : "系统检查未通过"}：${problem.message}`
        : "系统检查未通过。";
      alert.hidden = false;
    } else {
      alert.hidden = true;
    }
  } catch {
    renderChecks(checksNode, []);
    checksNode.hidden = false;
    const emptyRow = document.createElement("p");
    emptyRow.className = "panel-empty";
    emptyRow.textContent = "系统状态暂时不可读。";
    checksNode.replaceChildren(emptyRow);
    alert.hidden = true;
  }
  corpusNode.textContent = "—";
  try {
    const page = await api("/api/v1/runs?limit=1");
    const latest = page.items[0];
    if (latest) {
      const detail = await api(`/api/v1/runs/${encodeURIComponent(latest.run_id)}`);
      corpusNode.textContent = detail.research_context?.corpus_scope || "未记录";
    } else {
      corpusNode.textContent = "尚无研究记录";
    }
  } catch {
    corpusNode.textContent = "—";
  }
}

function makeRunRow(run) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "overview-run";
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
  date.textContent = formatDate(run.updated_at, true);
  meta.append(runState, date);
  button.append(title, meta);
  button.addEventListener("click", () => {
    window.location.hash = `#/research/${encodeURIComponent(run.run_id)}`;
  });
  return button;
}

async function renderRecentRuns() {
  const list = document.getElementById("overview-runs");
  const empty = document.getElementById("overview-runs-empty");
  try {
    const page = await api("/api/v1/runs?limit=5");
    const items = page.items || [];
    list.replaceChildren(...items.map(makeRunRow));
    list.hidden = !items.length;
    empty.hidden = Boolean(items.length);
  } catch {
    list.hidden = true;
    empty.hidden = false;
  }
}

async function mount() {
  await Promise.all([renderHealth(), renderRecentRuns()]);
}

registerView("overview", { mount });
