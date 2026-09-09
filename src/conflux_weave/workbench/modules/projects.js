/* Workbench Projects Section: Research Code Cognition & Governance Studio
   - Architecture Walkthrough & Native Flowchart Topology
   - Theory-to-Code Academic Mapping Matrix
   - Implementation & Contract Audit Engine
   - Semantic Branch Diff & Experiment Intent
   - Safe Code Stage & Controlled Decoupling Proposals
   Zero external dependencies, Vanilla ESM, offline-native. */

import { registerView, navigate } from "./router.js";

let projects = [];
let currentProjectId = null;
let currentProject = null;
let currentFilePath = null;
let currentProposal = null;
let currentWalkthrough = null;
let currentAuditReport = null;

const $ = (id) => document.getElementById(id);

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let errData = {};
    try {
      errData = await res.json();
    } catch {
      // ignore
    }
    const err = new Error(errData.message || `请求失败 (HTTP ${res.status})`);
    err.status = res.status;
    err.code = errData.code;
    throw err;
  }
  return res.json();
}

async function loadProjects() {
  try {
    projects = await fetchJSON("/api/v1/projects");
    renderProjectSelect();
    if (projects.length > 0) {
      const targetId = currentProjectId || projects[0].project_id;
      await selectProject(targetId);
    }
  } catch (err) {
    console.error("加载项目列表失败:", err);
  }
}

function renderProjectSelect() {
  const select = $("project-select");
  if (!select) return;
  select.innerHTML = "";
  for (const p of projects) {
    const opt = document.createElement("option");
    opt.value = p.project_id;
    opt.textContent = p.name;
    if (p.project_id === currentProjectId) opt.selected = true;
    select.appendChild(opt);
  }
}

async function selectProject(projectId) {
  currentProjectId = projectId;
  renderProjectSelect();
  try {
    currentProject = await fetchJSON(`/api/v1/projects/${projectId}`);
    renderGitCard(currentProject.git_status);
    await loadTree(projectId);
    await loadWalkthrough(projectId);
    await loadAuditReport(projectId);
  } catch (err) {
    console.error("加载项目详情失败:", err);
  }
}

function renderGitCard(git) {
  const branchEl = $("proj-git-branch");
  const dotEl = $("proj-git-status-dot");
  const infoEl = $("proj-git-commit-info");
  if (!branchEl || !dotEl || !infoEl) return;

  if (!git || !git.is_git) {
    branchEl.textContent = "无 Git 仓库";
    dotEl.className = "status-dot idle";
    infoEl.textContent = "当前目录未初始化为 Git 代码库";
    return;
  }

  branchEl.textContent = git.branch || "HEAD";
  dotEl.className = `status-dot ${git.is_dirty ? "running" : "completed"}`;
  dotEl.title = git.is_dirty ? "工作区有未提交变更" : "工作区干净";

  const headSha = git.commit_hash ? git.commit_hash.slice(0, 7) : "none";
  const commitMsg = git.commit_message || "无提交记录";
  infoEl.innerHTML = `<span class="git-sha">${headSha}</span> <span class="git-msg">${escapeHTML(commitMsg)}</span>`;
}

/* =========================================================================
   Center Tabs Management
   ========================================================================= */

function switchCenterTab(tabKey) {
  const tabs = {
    arch: { btn: $("tab-center-arch"), panel: $("panel-center-arch") },
    theory: { btn: $("tab-center-theory"), panel: $("panel-center-theory") },
    audit: { btn: $("tab-center-audit"), panel: $("panel-center-audit") },
    code: { btn: $("tab-center-code"), panel: $("panel-center-code") },
  };

  for (const [key, item] of Object.entries(tabs)) {
    if (!item.btn || !item.panel) continue;
    const isActive = key === tabKey;
    item.btn.classList.toggle("active", isActive);
    item.btn.setAttribute("aria-selected", isActive ? "true" : "false");
    item.panel.classList.toggle("active", isActive);
    item.panel.hidden = !isActive;
  }
}

function setupCenterTabs() {
  const map = [
    { id: "tab-center-arch", key: "arch" },
    { id: "tab-center-theory", key: "theory" },
    { id: "tab-center-audit", key: "audit" },
    { id: "tab-center-code", key: "code" },
  ];

  for (const item of map) {
    const btn = $(item.id);
    if (btn) {
      btn.addEventListener("click", () => switchCenterTab(item.key));
    }
  }
}

/* =========================================================================
   P3.2: Architecture Walkthrough & Native SVG Topology
   ========================================================================= */

async function loadWalkthrough(projectId) {
  const titleEl = $("walkthrough-proj-title");
  const overviewEl = $("walkthrough-proj-overview");
  const topoContainer = $("walkthrough-topology-container");
  const dataflowText = $("walkthrough-dataflow-text");
  const gridEl = $("walkthrough-components-grid");

  if (!topoContainer) return;
  topoContainer.innerHTML = '<span class="count-label">正在解构项目架构拓扑...</span>';

  try {
    const data = await fetchJSON(`/api/v1/projects/${projectId}/walkthrough`);
    currentWalkthrough = data;

    if (titleEl) titleEl.textContent = `《${currentProject ? currentProject.name : "项目"}》全景导读`;
    if (overviewEl) overviewEl.textContent = data.overview;
    if (dataflowText) dataflowText.innerHTML = formatMarkdown(data.data_flow_description);

    renderTopologyDiagram(data.components, topoContainer);
    renderComponentsGrid(data.components, gridEl);
    renderTheoryMappings(data.theory_mappings);
  } catch (err) {
    topoContainer.innerHTML = `<span class="form-error">架构解构失败: ${escapeHTML(err.message)}</span>`;
  }
}

function renderTopologyDiagram(components, container) {
  if (!container) return;

  const width = 760;
  const height = 360;

  // Render responsive native SVG flowchart
  const svg = `
  <svg class="native-topology-svg" viewBox="0 0 ${width} ${height}">
    <defs>
      <linearGradient id="grad-pres" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.15"/>
        <stop offset="100%" stop-color="#1d4ed8" stop-opacity="0.25"/>
      </linearGradient>
      <linearGradient id="grad-srv" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.15"/>
        <stop offset="100%" stop-color="#6d28d9" stop-opacity="0.25"/>
      </linearGradient>
      <linearGradient id="grad-eng" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#10b981" stop-opacity="0.15"/>
        <stop offset="100%" stop-color="#047857" stop-opacity="0.25"/>
      </linearGradient>
      <linearGradient id="grad-rag" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#f59e0b" stop-opacity="0.15"/>
        <stop offset="100%" stop-color="#b45309" stop-opacity="0.25"/>
      </linearGradient>
      <linearGradient id="grad-sto" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#64748b" stop-opacity="0.15"/>
        <stop offset="100%" stop-color="#334155" stop-opacity="0.25"/>
      </linearGradient>
      <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 1 L 10 5 L 0 9 z" fill="var(--text-secondary, #94a3b8)"/>
      </marker>
    </defs>

    <!-- Connectors -->
    <path d="M 380 70 L 380 110" stroke="var(--border-color, #475569)" stroke-width="2" marker-end="url(#arrow)"/>
    <path d="M 280 145 L 180 195" stroke="var(--border-color, #475569)" stroke-width="2" marker-end="url(#arrow)"/>
    <path d="M 480 145 L 580 195" stroke="var(--border-color, #475569)" stroke-width="2" marker-end="url(#arrow)"/>
    <path d="M 180 265 L 280 305" stroke="var(--border-color, #475569)" stroke-width="2" marker-end="url(#arrow)"/>
    <path d="M 580 265 L 480 305" stroke="var(--border-color, #475569)" stroke-width="2" marker-end="url(#arrow)"/>

    <!-- Node 1: Presentation -->
    <g class="topology-node" data-layer="presentation" transform="translate(260, 15)">
      <rect width="240" height="55" rx="8" fill="url(#grad-pres)" stroke="#3b82f6" stroke-width="1.5"/>
      <text x="120" y="24" text-anchor="middle" fill="var(--text-primary, #f1f5f9)" font-weight="600" font-size="13">展示与交互层 (Presentation)</text>
      <text x="120" y="44" text-anchor="middle" fill="var(--text-secondary, #94a3b8)" font-size="11">原生 ESM Workbench / 认知演播室</text>
    </g>

    <!-- Node 2: Service & API -->
    <g class="topology-node" data-layer="service" transform="translate(260, 110)">
      <rect width="240" height="60" rx="8" fill="url(#grad-srv)" stroke="#8b5cf6" stroke-width="1.5"/>
      <text x="120" y="25" text-anchor="middle" fill="var(--text-primary, #f1f5f9)" font-weight="600" font-size="13">服务与契约层 (Service & API)</text>
      <text x="120" y="46" text-anchor="middle" fill="var(--text-secondary, #94a3b8)" font-size="11">FastAPI REST & SSE / Pydantic 安全契约</text>
    </g>

    <!-- Node 3: Core Agents & Harness -->
    <g class="topology-node" data-layer="engine" transform="translate(40, 195)">
      <rect width="280" height="70" rx="8" fill="url(#grad-eng)" stroke="#10b981" stroke-width="1.5"/>
      <text x="140" y="25" text-anchor="middle" fill="var(--text-primary, #f1f5f9)" font-weight="600" font-size="13">确定性 Harness 与控制层</text>
      <text x="140" y="45" text-anchor="middle" fill="var(--text-secondary, #94a3b8)" font-size="11">DeterministicHarness / BudgetLedger</text>
      <text x="140" y="60" text-anchor="middle" fill="var(--text-secondary, #94a3b8)" font-size="10">ProjectAgent · CodingAgent · DocumentAgent</text>
    </g>

    <!-- Node 4: Multimodal RAG -->
    <g class="topology-node" data-layer="retrieval" transform="translate(440, 195)">
      <rect width="280" height="70" rx="8" fill="url(#grad-rag)" stroke="#f59e0b" stroke-width="1.5"/>
      <text x="140" y="25" text-anchor="middle" fill="var(--text-primary, #f1f5f9)" font-weight="600" font-size="13">多模态 RAG 与检索融合</text>
      <text x="140" y="45" text-anchor="middle" fill="var(--text-secondary, #94a3b8)" font-size="11">BM25 稀疏检索 + 密集向量索引</text>
      <text x="140" y="60" text-anchor="middle" fill="var(--text-secondary, #94a3b8)" font-size="10">倒数排序融合 (RRF) · 多模态图表抽取</text>
    </g>

    <!-- Node 5: Persistence & Storage -->
    <g class="topology-node" data-layer="storage" transform="translate(240, 295)">
      <rect width="280" height="55" rx="8" fill="url(#grad-sto)" stroke="#64748b" stroke-width="1.5"/>
      <text x="140" y="24" text-anchor="middle" fill="var(--text-primary, #f1f5f9)" font-weight="600" font-size="13">权威持久化与索引表族</text>
      <text x="140" y="44" text-anchor="middle" fill="var(--text-secondary, #94a3b8)" font-size="11">SQLite 单库单表溯源 · LanceDB 混合向量</text>
    </g>
  </svg>
  `;

  container.innerHTML = svg;

  // Bind click on nodes to inspect related files
  container.querySelectorAll(".topology-node").forEach((node) => {
    node.addEventListener("click", () => {
      const layer = node.dataset.layer;
      const comp = components.find((c) => c.layer === layer);
      if (comp && comp.files && comp.files.length > 0) {
        switchCenterTab("code");
        loadFile(comp.files[0]);
      }
    });
  });
}

function renderComponentsGrid(components, container) {
  if (!container) return;
  container.innerHTML = "";

  for (const comp of components) {
    const card = document.createElement("div");
    card.className = "component-card";
    const filesHtml = (comp.files || [])
      .map((f) => `<a class="comp-file-link" data-path="${escapeHTML(f)}">${escapeHTML(f)}</a>`)
      .join(" · ");

    card.innerHTML = `
      <div class="comp-card-header">
        <span class="comp-name">${escapeHTML(comp.name)}</span>
        <span class="comp-layer-badge">${escapeHTML(comp.layer)}</span>
      </div>
      <p class="comp-desc">${escapeHTML(comp.responsibilities)}</p>
      <div class="comp-files"><strong>核心文件：</strong> ${filesHtml || "无对应文件"}</div>
    `;

    card.querySelectorAll(".comp-file-link").forEach((link) => {
      link.addEventListener("click", () => {
        const p = link.dataset.path;
        if (p) {
          switchCenterTab("code");
          loadFile(p);
        }
      });
    });

    container.appendChild(card);
  }
}

/* =========================================================================
   P3.2: Theory-to-Code Academic Mapping Matrix
   ========================================================================= */

function renderTheoryMappings(mappings) {
  const container = $("theory-mappings-list");
  if (!container) return;
  container.innerHTML = "";

  if (!mappings || mappings.length === 0) {
    container.innerHTML = '<p class="count-label">暂未提取到理论映射项。</p>';
    return;
  }

  for (const item of mappings) {
    const card = document.createElement("div");
    card.className = "theory-mapping-card";

    card.innerHTML = `
      <div class="theory-card-header">
        <div class="theory-title-group">
          <h3 class="theory-concept">${escapeHTML(item.concept)}</h3>
          <span class="theory-ref-badge">${escapeHTML(item.paper_reference)}</span>
        </div>
        <button class="primary-button small-btn btn-jump-code" type="button" data-file="${escapeHTML(item.file_path)}" data-line="${item.line_number}">
          查看源码 (L${item.line_number})
        </button>
      </div>
      <div class="theory-code-line">
        <strong>代码实现标识：</strong> <code>${escapeHTML(item.code_symbol)}</code>
        <span class="theory-file-badge">${escapeHTML(item.file_path)}:${item.line_number}</span>
      </div>
      <p class="theory-desc">${escapeHTML(item.description)}</p>
      <div class="theory-rationale-box">
        <strong>💡 设计动机与学术考量：</strong> ${escapeHTML(item.design_rationale)}
      </div>
    `;

    const jumpBtn = card.querySelector(".btn-jump-code");
    if (jumpBtn) {
      jumpBtn.addEventListener("click", () => {
        switchCenterTab("code");
        loadFile(item.file_path, item.line_number);
      });
    }

    container.appendChild(card);
  }
}

/* =========================================================================
   P3.3: Implementation & Contract Audit Engine
   ========================================================================= */

async function loadAuditReport(projectId) {
  const scoreImpl = $("audit-score-impl");
  const scoreHealth = $("audit-score-health");
  const countFull = $("audit-count-full");
  const countMock = $("audit-count-mock");
  const countStub = $("audit-count-stub");
  const countUnimpl = $("audit-count-unimpl");
  const summaryEl = $("audit-summary-text");
  const findingsList = $("audit-findings-list");

  if (!findingsList) return;
  findingsList.innerHTML = '<span class="count-label">正在执行项目设计契约与代码体检...</span>';

  try {
    const report = await fetchJSON(`/api/v1/projects/${projectId}/audit`);
    currentAuditReport = report;

    if (scoreImpl) scoreImpl.textContent = String(report.implementation_score);
    if (scoreHealth) scoreHealth.textContent = String(report.health_score);

    const counts = report.status_counts || {};
    if (countFull) countFull.textContent = String(counts.fully_implemented || 0);
    if (countMock) countMock.textContent = String(counts.partially_implemented || 0);
    if (countStub) countStub.textContent = String(counts.stub_or_todo || 0);
    if (countUnimpl) countUnimpl.textContent = String(counts.unimplemented || 0);

    if (summaryEl) summaryEl.innerHTML = formatMarkdown(report.summary);

    renderAuditFindings(report.findings, findingsList);
  } catch (err) {
    findingsList.innerHTML = `<span class="form-error">体检失败: ${escapeHTML(err.message)}</span>`;
  }
}

function renderAuditFindings(findings, container) {
  container.innerHTML = "";
  if (!findings || findings.length === 0) {
    container.innerHTML = '<p class="count-label">🎉 代码质量优良，未发现明显坏味道或桩函数。</p>';
    return;
  }

  for (const f of findings) {
    const item = document.createElement("div");
    item.className = `audit-finding-item severity-${f.severity}`;

    const statusBadgeText = {
      fully_implemented: "完整落地",
      partially_implemented: "临时Mock",
      stub_or_todo: "空桩占位",
      unimplemented: "缺失未实现",
    }[f.implementation_status] || f.implementation_status;

    item.innerHTML = `
      <div class="finding-header">
        <div class="finding-title-row">
          <span class="severity-pill ${f.severity}">${f.severity.toUpperCase()}</span>
          <span class="category-pill">${escapeHTML(f.category)}</span>
          <span class="status-pill ${f.implementation_status}">${statusBadgeText}</span>
          <h4 class="finding-title">${escapeHTML(f.title)}</h4>
        </div>
        <div class="finding-actions">
          <button class="quiet-button small-btn btn-view-finding" data-file="${escapeHTML(f.target_file)}" data-line="${f.line_number || 1}">定位</button>
          <button class="primary-button small-btn btn-decouple-finding" data-file="${escapeHTML(f.target_file)}" data-rec="${escapeHTML(f.recommendation || f.title)}">生成解耦补丁</button>
        </div>
      </div>
      <p class="finding-desc">${escapeHTML(f.description)}</p>
      ${f.snippet ? `<pre class="finding-snippet"><code>${escapeHTML(f.snippet)}</code></pre>` : ""}
      <div class="finding-recommendation">
        <strong>💡 治理建议：</strong> ${escapeHTML(f.recommendation)}
      </div>
    `;

    const viewBtn = item.querySelector(".btn-view-finding");
    if (viewBtn) {
      viewBtn.addEventListener("click", () => {
        switchCenterTab("code");
        loadFile(f.target_file, f.line_number);
      });
    }

    const decoupleBtn = item.querySelector(".btn-decouple-finding");
    if (decoupleBtn) {
      decoupleBtn.addEventListener("click", () => {
        const tabCoding = $("tab-proj-coding");
        if (tabCoding) tabCoding.click();
        const targetInput = $("proj-coding-target");
        const instInput = $("proj-coding-instruction");
        if (targetInput) targetInput.value = f.target_file;
        if (instInput) instInput.value = f.recommendation || `解耦重构: ${f.title}`;
      });
    }

    container.appendChild(item);
  }
}

/* =========================================================================
   P3.2: Semantic Branch Diff Modal
   ========================================================================= */

async function handleSemanticDiff() {
  if (!currentProjectId) return;
  const dialog = $("project-semantic-diff-dialog");
  const curBranchEl = $("diff-cur-branch");
  const cmpBranchEl = $("diff-cmp-branch");
  const impactEl = $("diff-impact-level");
  const intentEl = $("diff-intent-text");
  const countEl = $("diff-file-count");
  const statEl = $("diff-lines-stat");
  const listEl = $("diff-files-list");

  if (!dialog) return;

  if (intentEl) intentEl.textContent = "正在计算分支间语义差异与实验意图...";
  if (listEl) listEl.innerHTML = "";
  dialog.showModal();

  try {
    const diff = await fetchJSON(`/api/v1/projects/${currentProjectId}/git/semantic-diff?compare_branch=main`);

    if (curBranchEl) curBranchEl.textContent = diff.current_branch || "HEAD";
    if (cmpBranchEl) cmpBranchEl.textContent = diff.compare_branch || "main";
    if (impactEl) {
      impactEl.textContent = diff.impact_level.toUpperCase();
      impactEl.className = `impact-level-pill impact-${diff.impact_level}`;
    }
    if (intentEl) intentEl.textContent = diff.experiment_intent;
    if (countEl) countEl.textContent = String((diff.file_diff_summaries || []).length);
    if (statEl) statEl.textContent = `+${diff.total_additions} / -${diff.total_deletions} 行`;

    if (listEl) {
      listEl.innerHTML = "";
      for (const item of diff.file_diff_summaries || []) {
        const row = document.createElement("div");
        row.className = "diff-file-row";
        row.innerHTML = `
          <div class="diff-file-left">
            <span class="diff-cat-pill">${escapeHTML(item.category)}</span>
            <a class="diff-file-name" data-path="${escapeHTML(item.file)}">${escapeHTML(item.file)}</a>
          </div>
          <div class="diff-file-right">
            <span class="diff-add">+${item.additions}</span>
            <span class="diff-del">-${item.deletions}</span>
          </div>
        `;

        row.querySelector(".diff-file-name").addEventListener("click", () => {
          dialog.close();
          switchCenterTab("code");
          loadFile(item.file);
        });

        listEl.appendChild(row);
      }
    }
  } catch (err) {
    if (intentEl) intentEl.textContent = `对比失败: ${err.message}`;
  }
}

/* =========================================================================
   Directory Tree & Safe File Reader
   ========================================================================= */

async function loadTree(projectId) {
  const treeEl = $("project-file-tree");
  const countEl = $("proj-file-count");
  if (!treeEl) return;
  treeEl.innerHTML = '<span class="count-label">正在扫描目录...</span>';

  try {
    const data = await fetchJSON(`/api/v1/projects/${projectId}/tree`);
    const items = data.items || [];
    if (countEl) countEl.textContent = `${items.length} 项`;
    treeEl.innerHTML = "";
    if (items.length === 0) {
      treeEl.innerHTML = '<span class="count-label">空目录或未发现有效文件</span>';
      return;
    }
    const ul = document.createElement("ul");
    ul.className = "file-tree-list";
    for (const node of items) {
      ul.appendChild(createTreeNodeElement(node));
    }
    treeEl.appendChild(ul);
  } catch (err) {
    treeEl.innerHTML = `<span class="form-error">目录读取失败: ${escapeHTML(err.message)}</span>`;
  }
}

function createTreeNodeElement(node) {
  const li = document.createElement("li");
  li.className = `tree-node ${node.is_dir ? "is-dir" : "is-file"}`;

  const row = document.createElement("div");
  row.className = "tree-node-row";

  if (node.is_dir) {
    row.innerHTML = `<span class="tree-icon">📁</span> <span class="tree-label">${escapeHTML(node.name)}</span>`;
    const childUl = document.createElement("ul");
    childUl.className = "tree-node-children";
    if (node.children && node.children.length > 0) {
      for (const child of node.children) {
        childUl.appendChild(createTreeNodeElement(child));
      }
    }
    row.addEventListener("click", () => {
      li.classList.toggle("collapsed");
    });
    li.appendChild(row);
    li.appendChild(childUl);
  } else {
    row.innerHTML = `<span class="tree-icon">📄</span> <span class="tree-label">${escapeHTML(node.name)}</span>`;
    row.addEventListener("click", () => {
      document.querySelectorAll(".tree-node-row.selected").forEach((el) => el.classList.remove("selected"));
      row.classList.add("selected");
      switchCenterTab("code");
      loadFile(node.path);
    });
    li.appendChild(row);
  }
  return li;
}

async function loadFile(filePath, highlightLine = null) {
  if (!currentProjectId || !filePath) return;
  currentFilePath = filePath;
  const pathEl = $("proj-current-file-path");
  const metaEl = $("proj-file-meta");
  const codeViewer = $("proj-code-viewer");
  const codingTarget = $("proj-coding-target");

  if (pathEl) pathEl.textContent = filePath;
  if (codingTarget) codingTarget.value = filePath;
  if (codeViewer) codeViewer.textContent = "正在读取文件内容...";

  try {
    const data = await fetchJSON(`/api/v1/projects/${currentProjectId}/file?path=${encodeURIComponent(filePath)}`);
    if (metaEl) {
      const kb = (data.size_bytes / 1024).toFixed(1);
      metaEl.textContent = `${kb} KB · SHA: ${data.sha256.slice(0, 8)}`;
    }
    if (codeViewer) {
      renderCodeWithLineNumbers(data.content, codeViewer, highlightLine);
    }
  } catch (err) {
    if (codeViewer) {
      codeViewer.textContent = `文件读取失败: ${err.message}`;
    }
  }
}

function renderCodeWithLineNumbers(rawCode, container, highlightLine = null) {
  container.innerHTML = "";
  const lines = rawCode.split("\n");
  const codeEl = document.createElement("code");
  const frag = document.createDocumentFragment();

  for (let i = 0; i < lines.length; i++) {
    const lineNumVal = i + 1;
    const lineRow = document.createElement("div");
    lineRow.className = "code-line-row";
    if (highlightLine && lineNumVal === highlightLine) {
      lineRow.classList.add("highlight-line");
    }

    const lineNum = document.createElement("span");
    lineNum.className = "code-line-num";
    lineNum.textContent = String(lineNumVal);

    const lineContent = document.createElement("span");
    lineContent.className = "code-line-content";
    lineContent.textContent = lines[i] || " ";

    lineRow.appendChild(lineNum);
    lineRow.appendChild(lineContent);
    frag.appendChild(lineRow);
  }
  codeEl.appendChild(frag);
  container.appendChild(codeEl);

  if (highlightLine) {
    const targetRow = container.querySelector(".highlight-line");
    if (targetRow) {
      targetRow.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }
}

/* =========================================================================
   ProjectAgent Q&A & CodingAgent Decoupling Proposals
   ========================================================================= */

async function handleAskProject() {
  const input = $("proj-qa-input");
  const btn = $("proj-qa-submit");
  const emptyEl = $("proj-qa-empty");
  const answerEl = $("proj-qa-answer");
  const evidenceEl = $("proj-qa-evidence");
  if (!input || !currentProjectId) return;

  const q = input.value.trim();
  if (!q) return;

  btn.disabled = true;
  btn.textContent = "思考分析中...";
  if (emptyEl) emptyEl.hidden = true;
  if (answerEl) {
    answerEl.hidden = false;
    answerEl.innerHTML = '<p class="count-label">ProjectAgent 正在分析项目结构与代码库上下文...</p>';
  }
  if (evidenceEl) evidenceEl.hidden = true;

  try {
    const res = await fetchJSON(`/api/v1/projects/${currentProjectId}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });

    if (answerEl) {
      answerEl.innerHTML = formatMarkdown(res.answer_markdown);
    }
    if (evidenceEl) {
      const cited = res.cited_files || [];
      if (cited.length > 0) {
        evidenceEl.hidden = false;
        evidenceEl.innerHTML = `<strong>引用代码文件：</strong> ` + cited.map((f) => `<a class="cited-file-link" data-path="${escapeHTML(f)}">${escapeHTML(f)}</a>`).join(" · ");
        evidenceEl.querySelectorAll(".cited-file-link").forEach((link) => {
          link.addEventListener("click", () => {
            const p = link.dataset.path;
            if (p) {
              switchCenterTab("code");
              loadFile(p);
            }
          });
        });
      }
    }
  } catch (err) {
    if (answerEl) {
      answerEl.innerHTML = `<p class="form-error">问答请求失败: ${escapeHTML(err.message)}</p>`;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "提问";
  }
}

async function handleProposeCoding() {
  const targetInput = $("proj-coding-target");
  const instInput = $("proj-coding-instruction");
  const btn = $("proj-coding-propose-btn");
  const propBox = $("proj-coding-proposal-box");
  const propTitle = $("proj-prop-title");
  const propRisk = $("proj-prop-risk");
  const propRationale = $("proj-prop-rationale");
  const diffContainer = $("proj-diff-container");
  const diffTitle = $("proj-diff-title");
  const diffViewer = $("proj-diff-viewer");

  if (!targetInput || !instInput || !currentProjectId) return;
  const target = targetInput.value.trim();
  const instruction = instInput.value.trim();
  if (!target || !instruction) {
    alert("请指定目标文件与解耦修改要求");
    return;
  }

  btn.disabled = true;
  btn.textContent = "生成解耦补丁中...";

  try {
    const proposal = await fetchJSON(`/api/v1/projects/${currentProjectId}/coding/propose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_file: target,
        instruction: instruction,
      }),
    });
    currentProposal = proposal;

    if (propBox) propBox.hidden = false;
    if (propTitle) propTitle.textContent = proposal.title;
    if (propRisk) {
      propRisk.textContent = `风险: ${proposal.risk_level.toUpperCase()}`;
      propRisk.className = `proposal-risk-badge risk-${proposal.risk_level}`;
    }
    if (propRationale) propRationale.textContent = proposal.rationale;

    switchCenterTab("code");
    if (diffContainer && diffViewer) {
      diffContainer.hidden = false;
      if (diffTitle) diffTitle.textContent = `${proposal.target_file} (${proposal.proposal_id})`;
      diffViewer.textContent = proposal.diff;
    }
  } catch (err) {
    alert(`补丁生成失败: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "生成解耦补丁提案";
  }
}

async function handleApplyProposal() {
  if (!currentProposal || !currentProjectId) return;
  const applyBtn = $("proj-prop-apply-btn");
  if (!confirm(`确定核准并原子应用解耦补丁至 ${currentProposal.target_file} 吗？\n\n注意：此操作受 SHA-256 乐观锁保护，若文件已被外部修改将安全阻断。`)) {
    return;
  }

  if (applyBtn) {
    applyBtn.disabled = true;
    applyBtn.textContent = "正在写入...";
  }

  try {
    const res = await fetchJSON(`/api/v1/projects/${currentProjectId}/coding/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        proposal_id: currentProposal.proposal_id,
        target_file: currentProposal.target_file,
        expected_hash: currentProposal.original_hash,
        proposed_content: currentProposal.proposed_content,
      }),
    });

    alert(res.message || "补丁已成功原子应用！");
    await loadFile(currentProposal.target_file);
    const diffContainer = $("proj-diff-container");
    if (diffContainer) diffContainer.hidden = true;
    const propBox = $("proj-coding-proposal-box");
    if (propBox) propBox.hidden = true;
    currentProposal = null;
    await loadAuditReport(currentProjectId);
  } catch (err) {
    alert(`写入失败: ${err.message}`);
  } finally {
    if (applyBtn) {
      applyBtn.disabled = false;
      applyBtn.textContent = "核准并原子应用";
    }
  }
}

function handleRejectProposal() {
  const propBox = $("proj-coding-proposal-box");
  const diffContainer = $("proj-diff-container");
  if (propBox) propBox.hidden = true;
  if (diffContainer) diffContainer.hidden = true;
  currentProposal = null;
}

function setupRightPaneTabs() {
  const tabQA = $("tab-proj-qa");
  const tabCoding = $("tab-proj-coding");
  const panelQA = $("proj-panel-qa");
  const panelCoding = $("proj-panel-coding");

  if (!tabQA || !tabCoding) return;

  tabQA.addEventListener("click", () => {
    tabQA.classList.add("active");
    tabQA.setAttribute("aria-selected", "true");
    tabCoding.classList.remove("active");
    tabCoding.setAttribute("aria-selected", "false");
    if (panelQA) panelQA.hidden = false;
    if (panelCoding) panelCoding.hidden = true;
  });

  tabCoding.addEventListener("click", () => {
    tabCoding.classList.add("active");
    tabCoding.setAttribute("aria-selected", "true");
    tabQA.classList.remove("active");
    tabQA.setAttribute("aria-selected", "false");
    if (panelCoding) panelCoding.hidden = false;
    if (panelQA) panelQA.hidden = true;
  });
}

function setupQuickChips() {
  document.querySelectorAll(".quick-chip-btn").forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.dataset.query;
      const input = $("proj-qa-input");
      if (input && q) {
        input.value = q;
        const tabQA = $("tab-proj-qa");
        if (tabQA) tabQA.click();
        handleAskProject();
      }
    });
  });
}

function setupSemanticDiffDialog() {
  const diffBtn = $("proj-btn-semantic-diff");
  const dialog = $("project-semantic-diff-dialog");
  const closeBtn = $("semantic-diff-close");

  if (diffBtn) diffBtn.addEventListener("click", handleSemanticDiff);
  if (closeBtn && dialog) closeBtn.addEventListener("click", () => dialog.close());
}

function setupRegisterDialog() {
  const openBtn = $("project-register-btn");
  const dialog = $("project-register-dialog");
  const closeBtn = $("project-register-close");
  const cancelBtn = $("reg-proj-cancel");
  const form = $("project-register-form");
  const errEl = $("reg-proj-error");

  if (!openBtn || !dialog || !form) return;

  openBtn.addEventListener("click", () => {
    form.reset();
    if (errEl) errEl.hidden = true;
    dialog.showModal();
  });

  const closeDialog = () => dialog.close();
  if (closeBtn) closeBtn.addEventListener("click", closeDialog);
  if (cancelBtn) cancelBtn.addEventListener("click", closeDialog);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("reg-proj-name").value.trim();
    const rootPath = $("reg-proj-path").value.trim();
    const desc = $("reg-proj-desc").value.trim();
    const submitBtn = $("reg-proj-submit");

    if (submitBtn) submitBtn.disabled = true;
    if (errEl) errEl.hidden = true;

    try {
      const newProj = await fetchJSON("/api/v1/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name,
          root_path: rootPath,
          description: desc,
        }),
      });
      dialog.close();
      await loadProjects();
      await selectProject(newProj.project_id);
    } catch (err) {
      if (errEl) {
        errEl.hidden = false;
        errEl.textContent = err.message;
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

function escapeHTML(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatMarkdown(text) {
  if (!text) return "";
  let html = escapeHTML(text);
  html = html.replace(/^#### (.*$)/gim, "<h4>$1</h4>");
  html = html.replace(/^### (.*$)/gim, "<h3>$1</h3>");
  html = html.replace(/^## (.*$)/gim, "<h2>$1</h2>");
  html = html.replace(/^# (.*$)/gim, "<h1>$1</h1>");
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/^\- (.*$)/gim, "<li>$1</li>");
  html = html.replace(/\n\n/g, "<br><br>");
  return html;
}

export async function mount() {
  const select = $("project-select");
  if (select) {
    select.addEventListener("change", (e) => {
      selectProject(e.target.value);
    });
  }

  const copyBtn = $("proj-file-copy-btn");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      const codeViewer = $("proj-code-viewer");
      if (codeViewer) {
        navigator.clipboard.writeText(codeViewer.textContent);
        copyBtn.textContent = "已复制";
        setTimeout(() => (copyBtn.textContent = "复制"), 1500);
      }
    });
  }

  const qaSubmit = $("proj-qa-submit");
  if (qaSubmit) qaSubmit.addEventListener("click", handleAskProject);

  const codingPropose = $("proj-coding-propose-btn");
  if (codingPropose) codingPropose.addEventListener("click", handleProposeCoding);

  const applyBtn = $("proj-prop-apply-btn");
  if (applyBtn) applyBtn.addEventListener("click", handleApplyProposal);

  const rejectBtn = $("proj-prop-reject-btn");
  if (rejectBtn) rejectBtn.addEventListener("click", handleRejectProposal);

  const reAuditBtn = $("btn-re-audit");
  if (reAuditBtn) {
    reAuditBtn.addEventListener("click", () => {
      if (currentProjectId) loadAuditReport(currentProjectId);
    });
  }

  setupCenterTabs();
  setupRightPaneTabs();
  setupQuickChips();
  setupSemanticDiffDialog();
  setupRegisterDialog();
  await loadProjects();
}

export async function unmount() {
  // Cleanup if needed
}

export async function onParam(param) {
  if (param && param !== currentProjectId) {
    await selectProject(param);
  }
}

registerView("projects", { mount, unmount, onParam });
