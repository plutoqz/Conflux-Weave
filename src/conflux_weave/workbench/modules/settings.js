import { api, formatDate, renderChecks, showToast } from "./shared.js?v=v0.3-report-render-4";
import { registerView } from "./router.js";

const form = document.getElementById("provider-form");
const banner = document.getElementById("settings-banner");
const errorNode = document.getElementById("settings-error");
const testResult = document.getElementById("test-result");
const testButton = document.getElementById("test-provider");
const saveButton = document.getElementById("save-provider");

const fields = {
  baseUrl: () => document.getElementById("cfg-base-url"),
  apiKey: () => document.getElementById("cfg-api-key"),
  model: () => document.getElementById("cfg-model"),
  embedding: () => document.getElementById("cfg-embedding"),
  reranker: () => document.getElementById("cfg-reranker"),
  contactEmail: () => document.getElementById("cfg-contact-email"),
};

function setBanner(kind, message) {
  banner.className = `settings-banner ${kind}`;
  banner.textContent = message;
  banner.hidden = !message;
}

function applyProviderDefaults(provider) {
  fields.baseUrl().value = provider.base_url || "";
  fields.model().value = provider.model || "";
  fields.embedding().value = provider.embedding_model || "";
  fields.reranker().value = provider.reranker_model || "";
  fields.contactEmail().value = provider.contact_email || "";
  const key = fields.apiKey();
  key.value = "";
  key.placeholder = provider.api_key_configured
    ? `已配置（${provider.api_key_hint || "已设置"}），留空保持不变`
    : "未配置";
}

function applyPaths(paths) {
  const container = document.getElementById("settings-paths");
  const labels = {
    database: "运行数据库",
    artifact_root: "工件库",
    workspace_root: "工作区",
    corpus_manifest: "语料清单",
    lancedb_root: "向量索引",
    dotenv: "配置文件",
  };
  const rows = Object.entries(labels).map(([key, label]) => {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = paths[key] || "—";
    dd.title = paths[key] || "";
    row.append(dt, dd);
    return row;
  });
  container.replaceChildren(...rows);
}

async function renderChecksSection() {
  const container = document.getElementById("settings-checks");
  try {
    const health = await api("/api/v1/health/ready");
    renderChecks(container, health.checks || []);
  } catch (error) {
    container.hidden = false;
    container.replaceChildren();
    const row = document.createElement("p");
    row.className = "panel-empty";
    row.textContent = `系统检查暂时不可读：${error.message}`;
    container.append(row);
  }
}

async function renderBudgetSection() {
  const container = document.getElementById("settings-budget");
  try {
    const page = await api("/api/v1/runs?limit=1");
    const latest = page.items[0];
    if (!latest) {
      container.textContent = "尚无研究记录，Run 预算将在第一次研究后显示。";
      return;
    }
    const detail = await api(`/api/v1/runs/${encodeURIComponent(latest.run_id)}`);
    const budget = detail.budget || {};
    const tokens = (budget.input_tokens_limit || 0) + (budget.output_tokens_limit || 0);
    container.textContent = [
      `最近 Run：${latest.run_id}`,
      `token 上限 ${tokens.toLocaleString("zh-CN")}`,
      `工具调用 ${budget.tool_calls_limit || 0}`,
      `检索轮次 ${budget.retrieval_rounds_limit || 0}`,
    ].join(" · ");
  } catch {
    container.textContent = "—";
  }
}

/* ---------- 分层记忆中心 (Memory Studio) ---------- */

const memTabs = {
  user: document.getElementById("mem-tab-user"),
  project: document.getElementById("mem-tab-project"),
  candidates: document.getElementById("mem-tab-candidates"),
};
const memPanels = {
  user: document.getElementById("mem-panel-user"),
  project: document.getElementById("mem-panel-project"),
  candidates: document.getElementById("mem-panel-candidates"),
};

function switchMemoryTab(tabName) {
  for (const [key, tab] of Object.entries(memTabs)) {
    if (!tab) continue;
    const active = key === tabName;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const [key, panel] of Object.entries(memPanels)) {
    if (!panel) continue;
    panel.hidden = key !== tabName;
  }
}

if (memTabs.user) memTabs.user.addEventListener("click", () => switchMemoryTab("user"));
if (memTabs.project) memTabs.project.addEventListener("click", () => switchMemoryTab("project"));
if (memTabs.candidates) memTabs.candidates.addEventListener("click", () => switchMemoryTab("candidates"));

async function renderMemoryStudio() {
  const statsBadge = document.getElementById("memory-stats-badge");
  const pendingPill = document.getElementById("mem-pending-pill");
  const userList = document.getElementById("user-memory-list");
  const projList = document.getElementById("project-memory-list");
  const candList = document.getElementById("candidate-memory-list");

  try {
    const [userRes, projRes, candRes] = await Promise.all([
      api("/api/v1/memories?scope=user&status=active"),
      api("/api/v1/memories?scope=project&status=active"),
      api("/api/v1/memories/candidates?status=pending"),
    ]);

    const userItems = userRes.items || [];
    const projItems = projRes.items || [];
    const candidates = candRes.items || candRes.candidates || [];

    const totalActive = userItems.length + projItems.length;
    if (statsBadge) {
      statsBadge.textContent = `${totalActive} 条已生效 · ${candidates.length} 待核准`;
    }
    if (pendingPill) {
      pendingPill.textContent = String(candidates.length);
      pendingPill.hidden = candidates.length === 0;
    }

    // 1. Render User Memories
    if (userList) {
      if (userItems.length === 0) {
        userList.innerHTML = '<p class="panel-empty">暂无用户偏好记录。可以在上方输入并记录，或在对话中表达偏好。</p>';
      } else {
        userList.innerHTML = userItems.map(item => `
          <div class="memory-card">
            <div class="memory-card-body">
              <div class="memory-card-header">
                <span class="memory-badge ${item.category}">${item.category === 'preference' ? '文风偏好' : item.category === 'constraint' ? '输出限制' : '事实'}</span>
                <span class="memory-card-meta">${formatDate(item.created_at, true)}</span>
              </div>
              <p class="memory-card-stmt">${escapeHtml(item.statement)}</p>
            </div>
            <div class="memory-card-actions">
              <button type="button" class="memory-card-del" data-id="${item.memory_id}" title="删除该记忆">删除</button>
            </div>
          </div>
        `).join("");
      }
    }

    // 2. Render Project Memories
    if (projList) {
      if (projItems.length === 0) {
        projList.innerHTML = '<p class="panel-empty">暂无项目约定记录。输入架构决策或约定后，Agent 将自动遵守。</p>';
      } else {
        projList.innerHTML = projItems.map(item => `
          <div class="memory-card">
            <div class="memory-card-body">
              <div class="memory-card-header">
                <span class="memory-badge ${item.category}">${item.category === 'decision' ? '架构决策' : '项目规约'}</span>
                <span class="memory-card-meta">${item.target_id} · ${formatDate(item.created_at, true)}</span>
              </div>
              <p class="memory-card-stmt">${escapeHtml(item.statement)}</p>
            </div>
            <div class="memory-card-actions">
              <button type="button" class="memory-card-del" data-id="${item.memory_id}" title="删除该约定">删除</button>
            </div>
          </div>
        `).join("");
      }
    }

    // 3. Render Pending Candidates (HITL)
    if (candList) {
      if (candidates.length === 0) {
        candList.innerHTML = '<p class="panel-empty">当前没有待核准的记忆候选。对话中表达偏好时会自动识别并呈现在此。</p>';
      } else {
        candList.innerHTML = candidates.map(c => `
          <div class="memory-card">
            <div class="memory-card-body">
              <div class="memory-card-header">
                <span class="memory-badge ${c.category}">候选 · ${c.scope === 'user' ? '用户偏好' : '项目约定'}</span>
                ${c.conflict_with_memory_id ? '<span class="memory-conflict-alert">检测到潜在语义冲突</span>' : ''}
                <span class="memory-card-meta">置信度 ${(c.confidence * 100).toFixed(0)}% · ${formatDate(c.created_at, true)}</span>
              </div>
              <p class="memory-card-stmt">${escapeHtml(c.statement)}</p>
            </div>
            <div class="memory-card-actions">
              <button type="button" class="memory-btn approve" data-cand-id="${c.candidate_id}">核准记住</button>
              <button type="button" class="memory-btn reject" data-cand-id="${c.candidate_id}">忽略</button>
            </div>
          </div>
        `).join("");
      }
    }

  } catch (err) {
    if (statsBadge) statsBadge.textContent = "记忆服务离线";
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

// Memory Actions Delegation
document.addEventListener("click", async (event) => {
  // Delete memory
  const delBtn = event.target.closest(".memory-card-del");
  if (delBtn && delBtn.dataset.id) {
    try {
      await api(`/api/v1/memories/${encodeURIComponent(delBtn.dataset.id)}`, { method: "DELETE" });
      showToast("已成功删除记忆");
      await renderMemoryStudio();
    } catch (e) {
      showToast(e.message || "删除失败");
    }
    return;
  }

  // Approve candidate
  const approveBtn = event.target.closest(".memory-btn.approve[data-cand-id]");
  if (approveBtn) {
    try {
      await api(`/api/v1/memories/candidates/${encodeURIComponent(approveBtn.dataset.candId)}/action`, {
        method: "POST",
        body: JSON.stringify({ action: "approve" }),
      });
      showToast("已核准并持久化至记忆中心");
      await renderMemoryStudio();
    } catch (e) {
      showToast(e.message || "核准失败");
    }
    return;
  }

  // Reject candidate
  const rejectBtn = event.target.closest(".memory-btn.reject[data-cand-id]");
  if (rejectBtn) {
    try {
      await api(`/api/v1/memories/candidates/${encodeURIComponent(rejectBtn.dataset.candId)}/action`, {
        method: "POST",
        body: JSON.stringify({ action: "reject" }),
      });
      showToast("已忽略该候选");
      await renderMemoryStudio();
    } catch (e) {
      showToast(e.message || "操作失败");
    }
    return;
  }
});

// Add user preference button
const addUserPrefBtn = document.getElementById("add-user-pref-btn");
const userPrefInput = document.getElementById("new-user-pref-input");
if (addUserPrefBtn && userPrefInput) {
  const handleAddPref = async () => {
    const text = userPrefInput.value.trim();
    if (!text) return;
    try {
      await api("/api/v1/memories", {
        method: "POST",
        body: JSON.stringify({
          scope: "user",
          target_id: "user_default",
          category: "preference",
          statement: text,
        }),
      });
      userPrefInput.value = "";
      showToast("已成功添加用户偏好记忆");
      await renderMemoryStudio();
    } catch (e) {
      showToast(e.message || "添加失败");
    }
  };
  addUserPrefBtn.addEventListener("click", handleAddPref);
  userPrefInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddPref();
    }
  });
}

// Add project convention button
const addProjConvBtn = document.getElementById("add-proj-convention-btn");
const projConvInput = document.getElementById("new-proj-convention-input");
if (addProjConvBtn && projConvInput) {
  const handleAddConv = async () => {
    const text = projConvInput.value.trim();
    if (!text) return;
    try {
      await api("/api/v1/memories", {
        method: "POST",
        body: JSON.stringify({
          scope: "project",
          target_id: "proj_default",
          category: "decision",
          statement: text,
        }),
      });
      projConvInput.value = "";
      showToast("已成功添加项目约定记忆");
      await renderMemoryStudio();
    } catch (e) {
      showToast(e.message || "添加失败");
    }
  };
  addProjConvBtn.addEventListener("click", handleAddConv);
  projConvInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddConv();
    }
  });
}

async function mount() {
  setBanner("", "");
  errorNode.hidden = true;
  testResult.textContent = "";
  try {
    const config = await api("/api/v1/config");
    applyProviderDefaults(config.provider);
    applyPaths(config.paths || {});
    const note = document.getElementById("provider-active-note");
    note.textContent = config.provider_active
      ? "当前进程已加载模型服务"
      : "当前进程未加载模型服务";
  } catch (error) {
    setBanner("warn", `配置服务不可用：${error.message}`);
  }
  await Promise.all([renderChecksSection(), renderBudgetSection(), renderMemoryStudio()]);
}

async function save(event) {
  event.preventDefault();
  errorNode.hidden = true;
  setBanner("", "");
  saveButton.disabled = true;
  const payload = {
    base_url: fields.baseUrl().value.trim(),
    model: fields.model().value.trim(),
    embedding_model: fields.embedding().value.trim(),
    reranker_model: fields.reranker().value.trim(),
    contact_email: fields.contactEmail().value.trim(),
  };
  const key = fields.apiKey().value.trim();
  if (key) payload.api_key = key;
  try {
    const result = await api("/api/v1/config/provider", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    applyProviderDefaults(result.provider);
    setBanner("ok", `${result.message} 重启 conflux-weave serve 后生效。`);
  } catch (error) {
    errorNode.textContent = error.recoveryAction || error.message;
    errorNode.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
}

async function testConnection() {
  errorNode.hidden = true;
  testResult.textContent = "正在连接…";
  testButton.disabled = true;
  const payload = {
    base_url: fields.baseUrl().value.trim() || null,
    model: fields.model().value.trim() || null,
  };
  const key = fields.apiKey().value.trim();
  if (key) payload.api_key = key;
  try {
    const result = await api("/api/v1/config/provider/test", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    testResult.textContent = result.ok
      ? `连接成功 · ${result.message}`
      : `连接失败 · ${result.message}`;
  } catch (error) {
    testResult.textContent = "";
    errorNode.textContent = error.recoveryAction || error.message;
    errorNode.hidden = false;
  } finally {
    testButton.disabled = false;
  }
}

form.addEventListener("submit", save);
testButton.addEventListener("click", testConnection);

registerView("settings", { mount });
