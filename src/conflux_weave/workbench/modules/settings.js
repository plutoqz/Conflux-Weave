/* 设置视图：模型服务配置（写入本地 .env，重启生效）、系统检查与数据目录。 */

import { api, renderChecks } from "./shared.js?v=v0.3-report-render-4";
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
  await Promise.all([renderChecksSection(), renderBudgetSection()]);
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
