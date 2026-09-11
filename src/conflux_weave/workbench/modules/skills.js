/* 技能工坊演播室 (Skill Studio) - 原生 ESM 实现 (P5.5) */

import { $, renderAnswer, showToast } from "./shared.js";
import { registerView } from "./router.js";

let allSkills = [];
let currentCategory = "all";
let activeSkill = null;

async function fetchSkills() {
  try {
    const res = await fetch("/api/v1/skills");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allSkills = data.items || [];
    renderSkillCards();
  } catch (err) {
    showToast(`获取技能列表失败: ${err.message}`, "error");
  }
}

function renderSkillCards() {
  const container = $("#skills-card-grid");
  if (!container) return;

  const filtered = currentCategory === "all"
    ? allSkills
    : allSkills.filter((s) => s.category === currentCategory);

  if (filtered.length === 0) {
    container.innerHTML = `<p class="panel-empty" style="grid-column: 1 / -1;">暂无该分类下的可用技能</p>`;
    return;
  }

  container.innerHTML = filtered.map((skill) => `
    <div class="skill-card-item">
      <div>
        <div class="skill-card-header">
          <h3>${skill.name}</h3>
          <span class="skill-badge ${skill.category}">${skill.category}</span>
        </div>
        <p class="skill-card-desc">${skill.description}</p>
        
        <div class="skill-card-meta">
          <div class="skill-meta-row">
            <span>作者: ${skill.author}</span>
            <span>v${skill.version}</span>
          </div>
          <div class="skill-tools-tags">
            ${(skill.required_tools || []).map((t) => `<span class="skill-tool-tag">${t}</span>`).join("")}
          </div>
        </div>
      </div>

      <div class="skill-card-footer">
        <span>预算: ≤${skill.default_budget?.max_tokens || 8000} tok</span>
        <button type="button" class="primary-button small-btn run-skill-btn" data-skill-id="${skill.skill_id}">
          运行工作流
        </button>
      </div>
    </div>
  `).join("");

  container.querySelectorAll(".run-skill-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const skillId = btn.dataset.skillId;
      openSkillRunner(skillId);
    });
  });
}

async function openSkillRunner(skillId) {
  const modal = $("#skill-runner-modal");
  if (!modal) return;

  try {
    const res = await fetch(`/api/v1/skills/${skillId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    activeSkill = await res.json();
  } catch (err) {
    showToast(`获取技能详情失败: ${err.message}`, "error");
    return;
  }

  $("#skill-modal-title").textContent = activeSkill.name;
  $("#skill-modal-desc").textContent = activeSkill.description;
  $("#skill-modal-output").innerHTML = `<div class="panel-empty">点击“启动执行”后，此处将呈现结构化产出成果</div>`;
  $("#skill-modal-metrics").hidden = true;

  // Render input fields based on input_schema
  const formContainer = $("#skill-modal-form");
  const props = activeSkill.input_schema?.properties || {};
  const required = activeSkill.input_schema?.required || [];

  formContainer.innerHTML = Object.entries(props).map(([key, schema]) => {
    const isRequired = required.includes(key);
    const desc = schema.description || "";
    const defaultValue = schema.default || "";

    if (schema.type === "array") {
      return `
        <div class="field-group" style="margin-bottom: 12px;">
          <label class="field-label" style="display: block; margin-bottom: 4px;">
            ${key} ${isRequired ? '<span style="color: var(--terra, #e11d48);">*</span>' : ""}
            <span class="field-hint">(${desc || "逗号分隔数组"})</span>
          </label>
          <input type="text" name="${key}" class="field-input" placeholder="例如: 2606.08702, 2606.10209" value="${Array.isArray(defaultValue) ? defaultValue.join(", ") : defaultValue}" ${isRequired ? "required" : ""}>
        </div>
      `;
    }

    if (schema.type === "string" && (key.includes("content") || key.includes("prompt"))) {
      return `
        <div class="field-group" style="margin-bottom: 12px;">
          <label class="field-label" style="display: block; margin-bottom: 4px;">
            ${key} ${isRequired ? '<span style="color: var(--terra, #e11d48);">*</span>' : ""}
            <span class="field-hint">(${desc})</span>
          </label>
          <textarea name="${key}" rows="4" class="field-input" style="font-family: var(--mono); font-size: 12px;" placeholder="${desc}" ${isRequired ? "required" : ""}>${defaultValue}</textarea>
        </div>
      `;
    }

    return `
      <div class="field-group" style="margin-bottom: 12px;">
        <label class="field-label" style="display: block; margin-bottom: 4px;">
          ${key} ${isRequired ? '<span style="color: var(--terra, #e11d48);">*</span>' : ""}
          <span class="field-hint">(${desc})</span>
        </label>
        <input type="text" name="${key}" class="field-input" placeholder="${desc}" value="${defaultValue}" ${isRequired ? "required" : ""}>
      </div>
    `;
  }).join("");

  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.hidden = false;
  }
}

async function executeCurrentSkill(e) {
  e.preventDefault();
  if (!activeSkill) return;

  const form = $("#skill-modal-form");
  const inputs = {};
  const props = activeSkill.input_schema?.properties || {};

  new FormData(form).forEach((value, key) => {
    const valStr = String(value).trim();
    if (!valStr) return;
    const schema = props[key] || {};
    if (schema.type === "array") {
      inputs[key] = valStr.split(",").map((s) => s.trim()).filter(Boolean);
    } else if (schema.type === "integer" || schema.type === "number") {
      inputs[key] = Number(valStr);
    } else {
      inputs[key] = valStr;
    }
  });

  const submitBtn = $("#skill-modal-submit-btn");
  submitBtn.disabled = true;
  submitBtn.textContent = "执行中...";

  const outputContainer = $("#skill-modal-output");
  outputContainer.innerHTML = `
    <div class="flex items-center justify-center py-12 text-xs text-muted-foreground animate-pulse">
      正在调用大模型并协同执行学术技能工作流...
    </div>
  `;

  try {
    const res = await fetch(`/api/v1/skills/${activeSkill.skill_id}/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.message || `HTTP ${res.status}`);
    }

    // Show Metrics
    $("#skill-modal-metrics").hidden = false;
    $("#skill-metric-tokens").textContent = `${data.tokens_consumed} tokens`;
    $("#skill-metric-time").textContent = `${data.elapsed_seconds}s`;
    $("#skill-metric-status").textContent = data.status;

    // Render markdown report
    outputContainer.innerHTML = "";
    renderAnswer(outputContainer, data.content);
    showToast("技能工作流执行完毕！", "success");
  } catch (err) {
    outputContainer.innerHTML = `
      <div class="p-4 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-600 text-xs">
        <strong>执行失败:</strong> ${err.message}
      </div>
    `;
    showToast(`执行失败: ${err.message}`, "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "启动执行";
  }
}

export function mount() {
  const container = $("#skills-view");
  if (!container) return;

  // Bind category filter tabs
  container.querySelectorAll(".skill-category-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      container.querySelectorAll(".skill-category-pill").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      currentCategory = pill.dataset.category || "all";
      renderSkillCards();
    });
  });

  // Bind modal events
  $("#skill-modal-close")?.addEventListener("click", () => {
    const modal = $("#skill-runner-modal");
    if (modal) {
      if (typeof modal.close === "function") modal.close();
      else modal.hidden = true;
    }
  });
  $("#skill-modal-form")?.addEventListener("submit", executeCurrentSkill);

  fetchSkills();
}

export function unmount() {
  activeSkill = null;
  const modal = $("#skill-runner-modal");
  if (modal) {
    if (typeof modal.close === "function") modal.close();
    else modal.hidden = true;
  }
}

registerView("skills", { mount, unmount });
