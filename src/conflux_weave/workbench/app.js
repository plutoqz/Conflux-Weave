import {
  $,
  api,
  familyLabels,
  formatDate,
  initTheme,
  modeLabels,
  renderAnswer,
  showToast,
  stateLabels,
} from "./modules/shared.js?v=v0.3-report-render-4";
import { initRouter, navigate, registerView, replaceHash } from "./modules/router.js";
import "./modules/overview.js";
import "./modules/chat.js";
import "./modules/library.js?v=v0.3-library-ux-3";
import "./modules/settings.js";
import { createSparkbar } from "./modules/charts.js";

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

let runFilter = "all";
let runSearchQuery = "";

function filterRuns(runs) {
  return runs.filter((run) => {
    if (runFilter === "completed" && run.state !== "completed" && run.state !== "complete") return false;
    if (runFilter === "active" && ["completed", "complete", "cancelled", "failed", "expired"].includes(run.state)) return false;
    if (runSearchQuery) {
      const q = (run.query || "").toLowerCase();
      const m = (run.status_message || "").toLowerCase();
      const s = runSearchQuery.toLowerCase();
      if (!q.includes(s) && !m.includes(s)) return false;
    }
    return true;
  });
}

function makeRunItem(run) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `run-item${state.selected?.run_id === run.run_id ? " selected" : ""}`;
  button.dataset.runId = run.run_id;

  const header = document.createElement("div");
  header.className = "run-item-top";

  const dot = document.createElement("span");
  dot.className = `run-status-dot ${run.state}`;
  dot.setAttribute("aria-hidden", "true");

  const title = document.createElement("strong");
  title.className = "run-item-title";
  title.textContent = run.query || "未命名研究";
  button.title = run.query || "未命名研究";
  header.append(dot, title);

  const meta = document.createElement("span");
  meta.className = "item-meta";
  const runState = document.createElement("span");
  runState.className = `mini-state ${run.state}`;
  runState.textContent = stateLabels[run.state] || "状态更新";
  const date = document.createElement("span");
  date.textContent = formatDate(run.updated_at);
  meta.append(runState, date);

  button.append(header, meta);
  button.addEventListener("click", () => selectRun(run.run_id));
  return button;
}

function renderRuns() {
  const list = $("#run-list");
  const filtered = filterRuns(state.runs);
  list.replaceChildren(...filtered.map(makeRunItem));
  $("#load-more").hidden = !state.nextCursor || Boolean(runSearchQuery) || runFilter !== "all";
}

function updateSelectedRun(runId) {
  const list = $("#run-list");
  if (!list) return;
  list.querySelectorAll(".run-item").forEach((btn) => {
    btn.classList.toggle("selected", btn.dataset.runId === runId);
  });
}

const artifactCache = new Map();

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
  const inputTokens = budget.input_tokens_used || 0;
  const outputTokens = budget.output_tokens_used || 0;
  const tokens = inputTokens + outputTokens;
  const prevTokens = (prevBudget.input_tokens_used || 0) + (prevBudget.output_tokens_used || 0);
  const tokenLimit = (budget.input_tokens_limit || 0) + (budget.output_tokens_limit || 0);
  $("#token-value").textContent = `${tokens.toLocaleString("zh-CN")} tokens`;
  $("#budget-state").textContent = tokenLimit ? `上限 ${tokenLimit.toLocaleString("zh-CN")}` : "未记录上限";

  const tokenSpark = $("#token-sparkbar");
  if (tokenSpark) {
    tokenSpark.replaceChildren(createSparkbar({
      segments: [
        { label: "输入", value: inputTokens, color: "var(--seance)" },
        { label: "输出", value: outputTokens, color: "var(--moss)" },
      ],
      height: 5,
    }));
  }

  $("#retrieval-value").textContent = `${budget.retrieval_rounds_used || 0} / ${budget.retrieval_rounds_limit || 0}`;
  $("#tool-value").textContent = `工具调用 ${budget.tool_calls_used || 0} / ${budget.tool_calls_limit || 0}`;

  const retrievalSpark = $("#retrieval-sparkbar");
  if (retrievalSpark) {
    const roundsUsed = budget.retrieval_rounds_used || 0;
    const toolsUsed = budget.tool_calls_used || 0;
    retrievalSpark.replaceChildren(createSparkbar({
      segments: [
        { label: "轮次", value: roundsUsed, color: "var(--moss-deep)" },
        { label: "调用", value: toolsUsed, color: "var(--report-purple)" },
      ],
      height: 5,
    }));
  }
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
  updateSelectedRun(run.run_id);
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

function bindDocumentToolbar(run, content, evidenceIds) {
  const toolbar = $("#document-toolbar");
  if (!toolbar || !content) return;
  toolbar.hidden = false;
  const wordCount = (content.match(/[\u4e00-\u9fa5]|\b[a-zA-Z]+\b/g) || []).length;
  const readMin = Math.max(1, Math.round(wordCount / 280));
  const evCount = evidenceIds.length;
  const stats = $("#doc-meta-stats");
  if (stats) {
    stats.textContent = `约 ${wordCount.toLocaleString("zh-CN")} 字 · 读完约 ${readMin} 分钟 · ${evCount} 条证据`;
  }

  const copyBtn = $("#doc-copy-markdown");
  if (copyBtn) {
    copyBtn.onclick = async () => {
      try {
        await navigator.clipboard.writeText(content);
        const orig = copyBtn.innerHTML;
        copyBtn.innerHTML = `
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:#10b981;"><polyline points="20 6 9 17 4 12"/></svg>
          <span style="color:#10b981;">已复制全文</span>
        `;
        setTimeout(() => { copyBtn.innerHTML = orig; }, 2000);
      } catch {}
    };
  }

  const dlBtn = $("#doc-download-markdown");
  if (dlBtn) {
    dlBtn.onclick = () => {
      const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const safeName = (run.query || "research-report").replace(/[/\\?%*:|"<>]/g, "_").slice(0, 40);
      a.href = url;
      a.download = `${safeName}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    };
  }
}

async function loadDelivery(run) {
  const answer = $("#answer-content");
  const loading = $("#answer-loading");
  const empty = $("#answer-empty");

  const artifactIds = run.delivery?.artifact_ids || [];
  if (!artifactIds.length) {
    loading.hidden = true;
    answer.hidden = true;
    answer.classList.remove("loading-transition");
    empty.hidden = false;
    $("#document-toolbar")?.setAttribute("hidden", "");
    return;
  }

  // Instant cache hit for completed runs: zero network latency, zero layout shift, zero flicker
  const cacheKey = `${run.run_id}:${artifactIds.join(",")}`;
  const cached = artifactCache.get(cacheKey);
  if (cached) {
    loading.hidden = true;
    empty.hidden = true;
    renderAnswer(answer, cached.content, cached.mediaType);
    answer.hidden = false;
    answer.classList.remove("loading-transition");
    const evidenceIds = run.delivery?.evidence_ids || [];
    loadEvidence(run.run_id, evidenceIds);
    bindDocumentToolbar(run, cached.content, evidenceIds);
    return;
  }

  // Non-collapsing transition: keep current content visible while dimmed during fetch
  if (!answer.hidden && answer.children.length > 0) {
    answer.classList.add("loading-transition");
    loading.hidden = false;
  } else {
    loading.hidden = false;
    answer.hidden = true;
  }
  empty.hidden = true;

  const evidenceIds = run.delivery?.evidence_ids || [];
  loadEvidence(run.run_id, evidenceIds);

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
    if (run.is_terminal) {
      artifactCache.set(cacheKey, { content, mediaType: report.artifact.media_type });
    }
    renderAnswer(answer, content, report.artifact.media_type);
    answer.hidden = false;
    bindDocumentToolbar(run, content, evidenceIds);
  } catch (error) {
    answer.hidden = true;
    empty.hidden = false;
    $("#document-toolbar")?.setAttribute("hidden", "");
    $("#answer-empty strong").textContent = "交付结果暂时不可读";
    $("#answer-empty span").textContent = error.message;
  } finally {
    loading.hidden = true;
    answer.classList.remove("loading-transition");
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
    const isImage = item.modality === "image" || Boolean(item.asset_id);
    const title = document.createElement("strong");
    title.textContent = `证据 ${index + 1}${isImage ? " · 图片" : ""}`;
    const quote = document.createElement("span");
    quote.textContent = item.quote || (isImage ? "（图片证据）" : "（无正文）");
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

async function renderEvidenceData(prefix, item, index) {
  const isImage = item.modality === "image" || Boolean(item.asset_id);
  $(`#${prefix}-title`).textContent = `证据 ${index + 1}${isImage ? " (图片)" : ""}`;
  $(`#${prefix}-source`).textContent = item.source_snapshot_id;
  $(`#${prefix}-method`).textContent = item.extraction_method;
  $(`#${prefix}-locator`).textContent = JSON.stringify(item.locator, null, 2);

  const imgContainer = $(`#${prefix}-image-container`);
  const imgToolbar = $(`#${prefix}-image-toolbar`);
  const tabOrig = $(`#${prefix}-tab-orig`);
  const tabThumb = $(`#${prefix}-tab-thumb`);
  const imgLink = $(`#${prefix}-image-link`);
  const imgEl = $(`#${prefix}-image`);
  const imgError = $(`#${prefix}-image-error`);
  const warningsEl = $(`#${prefix}-warnings`);
  const captionCard = $(`#${prefix}-caption-card`);
  const captionEl = $(`#${prefix}-caption`);
  const quoteEl = $(`#${prefix}-quote`);
  const pageRow = $(`#${prefix}-page-row`);
  const pageEl = $(`#${prefix}-page`);
  const bboxRow = $(`#${prefix}-bbox-row`);
  const bboxEl = $(`#${prefix}-bbox`);
  const parentChunksRow = $(`#${prefix}-parent-chunks-row`);
  const parentChunksEl = $(`#${prefix}-parent-chunks`);
  const assetRow = $(`#${prefix}-asset-row`);
  const assetIdEl = $(`#${prefix}-asset-id`);
  const artifactRow = $(`#${prefix}-artifact-row`);
  const artifactRefEl = $(`#${prefix}-artifact-ref`);
  const thumbRefRow = $(`#${prefix}-thumb-ref-row`);
  const thumbRefEl = $(`#${prefix}-thumb-artifact-ref`);

  if (!isImage) {
    if (imgContainer) imgContainer.hidden = true;
    if (warningsEl) warningsEl.hidden = true;
    if (captionCard) captionCard.hidden = true;
    if (quoteEl) {
      quoteEl.hidden = false;
      quoteEl.textContent = item.quote || "";
    }
    if (pageRow) pageRow.hidden = true;
    if (bboxRow) bboxRow.hidden = true;
    if (parentChunksRow) parentChunksRow.hidden = true;
    if (assetRow) assetRow.hidden = true;
    if (artifactRow) artifactRow.hidden = true;
    if (thumbRefRow) thumbRefRow.hidden = true;
    return;
  }

  // Image mode setup
  if (imgContainer) imgContainer.hidden = false;
  if (imgError) imgError.hidden = true;
  if (captionCard) captionCard.hidden = false;
  if (captionEl) captionEl.textContent = item.quote || "（无图注）";
  if (quoteEl) quoteEl.hidden = true;

  // Initial fallbacks from item metadata
  const initPage = item.locator?.page;
  if (pageRow) {
    if (initPage) {
      pageRow.hidden = false;
      if (pageEl) pageEl.textContent = `第 ${initPage} 页`;
    } else {
      pageRow.hidden = true;
    }
  }

  const initBbox = item.locator?.bbox;
  if (bboxRow) {
    if (initBbox && typeof initBbox === "object") {
      bboxRow.hidden = false;
      if (bboxEl) bboxEl.textContent = `x: ${initBbox.x}, y: ${initBbox.y}, w: ${initBbox.width}, h: ${initBbox.height}`;
    } else {
      bboxRow.hidden = true;
    }
  }

  if (assetRow) {
    if (item.asset_id) {
      assetRow.hidden = false;
      if (assetIdEl) assetIdEl.textContent = item.asset_id;
    } else {
      assetRow.hidden = true;
    }
  }

  if (artifactRow) {
    if (item.artifact_ref) {
      artifactRow.hidden = false;
      if (artifactRefEl) artifactRefEl.textContent = item.artifact_ref;
    } else {
      artifactRow.hidden = true;
    }
  }

  let origUrl = item.asset_id ? `/api/v1/library/assets/${encodeURIComponent(item.asset_id)}/content?variant=original` : "";
  let thumbUrl = item.asset_id ? `/api/v1/library/assets/${encodeURIComponent(item.asset_id)}/content?variant=thumbnail` : "";

  function setVariant(variant) {
    if (!imgEl) return;
    imgEl.hidden = false;
    if (imgError) imgError.hidden = true;
    if (variant === "thumbnail") {
      imgEl.src = thumbUrl;
      if (tabThumb) { tabThumb.classList.add("active"); tabThumb.setAttribute("aria-selected", "true"); }
      if (tabOrig) { tabOrig.classList.remove("active"); tabOrig.setAttribute("aria-selected", "false"); }
      if (imgLink) imgLink.href = thumbUrl;
    } else {
      imgEl.src = origUrl;
      if (tabOrig) { tabOrig.classList.add("active"); tabOrig.setAttribute("aria-selected", "true"); }
      if (tabThumb) { tabThumb.classList.remove("active"); tabThumb.setAttribute("aria-selected", "false"); }
      if (imgLink) imgLink.href = origUrl;
    }
  }

  if (tabOrig) {
    tabOrig.onclick = (e) => { e.preventDefault(); setVariant("original"); };
  }
  if (tabThumb) {
    tabThumb.onclick = (e) => { e.preventDefault(); setVariant("thumbnail"); };
  }

  if (imgEl) {
    imgEl.hidden = false;
    imgEl.alt = item.quote || "证据图片";
    imgEl.onerror = () => {
      imgEl.hidden = true;
      if (imgError) {
        imgError.hidden = false;
        imgError.textContent = "图片加载失败或该资产无可用图片。";
      }
    };
    if (origUrl) {
      setVariant("original");
    }
  }

  if (item.asset_id) {
    try {
      const detail = await api(`/api/v1/library/assets/${encodeURIComponent(item.asset_id)}`);

      if (detail.content_url) origUrl = detail.content_url;
      if (detail.thumbnail_url) thumbUrl = detail.thumbnail_url;

      if (imgToolbar) {
        imgToolbar.hidden = false;
        if (imgLink) imgLink.href = origUrl;
        if (tabThumb) {
          tabThumb.style.display = detail.thumbnail_artifact_ref ? "inline-block" : "none";
        }
      }

      const warnings = Array.isArray(detail.warnings) ? [...detail.warnings] : [];
      if (warnings.length && warningsEl) {
        warningsEl.hidden = false;
        warningsEl.replaceChildren(...warnings.map((w) => {
          const tag = document.createElement("span");
          tag.className = "evidence-warning-tag";
          tag.textContent = w;
          return tag;
        }));
      } else if (warningsEl) {
        warningsEl.hidden = true;
      }

      if (detail.extraction_status === "failed") {
        if (imgEl) imgEl.hidden = true;
        if (imgError) {
          imgError.hidden = false;
          imgError.textContent = `图片提取降级/失败: ${warnings.join(", ") || "render_failed"}`;
        }
      }

      if (captionEl) {
        captionEl.textContent = detail.caption || item.quote || "（无图注）";
      }

      const pageVal = detail.page || initPage;
      if (pageRow) {
        if (pageVal) {
          pageRow.hidden = false;
          if (pageEl) pageEl.textContent = `第 ${pageVal} 页`;
        } else {
          pageRow.hidden = true;
        }
      }

      const b = detail.bbox || initBbox;
      if (bboxRow) {
        if (b && typeof b === "object") {
          bboxRow.hidden = false;
          const space = detail.coordinate_space ? ` (${detail.coordinate_space})` : "";
          if (bboxEl) bboxEl.textContent = `x: ${b.x}, y: ${b.y}, w: ${b.width}, h: ${b.height}${space}`;
        } else {
          bboxRow.hidden = true;
        }
      }

      const parentIds = Array.isArray(detail.parent_segment_ids) && detail.parent_segment_ids.length > 0
        ? detail.parent_segment_ids
        : [];
      if (parentChunksRow) {
        parentChunksRow.hidden = false;
        if (parentChunksEl) {
          if (parentIds.length > 0) {
            parentChunksEl.replaceChildren(...parentIds.map((pid) => {
              const chip = document.createElement("span");
              chip.className = "evidence-chunk-tag";
              chip.textContent = pid;
              return chip;
            }));
          } else {
            parentChunksEl.textContent = "无关联父 Chunk";
          }
        }
      }

      if (detail.source_snapshot_id) $(`#${prefix}-source`).textContent = detail.source_snapshot_id;
      if (detail.extraction_method) $(`#${prefix}-method`).textContent = detail.extraction_method;

      const artRef = detail.artifact_ref || item.artifact_ref;
      if (artifactRow) {
        if (artRef) {
          artifactRow.hidden = false;
          if (artifactRefEl) artifactRefEl.textContent = artRef;
        } else {
          artifactRow.hidden = true;
        }
      }

      if (thumbRefRow) {
        if (detail.thumbnail_artifact_ref) {
          thumbRefRow.hidden = false;
          if (thumbRefEl) thumbRefEl.textContent = detail.thumbnail_artifact_ref;
        } else {
          thumbRefRow.hidden = true;
        }
      }
    } catch {
      if (imgToolbar) imgToolbar.hidden = false;
    }
  } else {
    if (warningsEl) warningsEl.hidden = true;
    if (thumbRefRow) thumbRefRow.hidden = true;
  }
}

function openEvidence(item, index, trigger) {
  if (inspectorMedia.matches) {
    openInspector(index, trigger);
    return;
  }
  renderEvidenceData("evidence", item, index);
  $("#evidence-dialog").showModal();
}

function openInspector(index, trigger = state.inspectorTrigger) {
  state.inspectorIndex = index;
  state.inspectorTrigger = trigger;
  const item = state.evidenceList[index];
  if (!item) return;
  renderEvidenceData("insp", item, index);
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
  if (event.key === "[" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
    toggleSidebar();
  }
});
document.addEventListener("click", (event) => {
  const citation = event.target.closest(".citation-link");
  if (!citation) return;
  const index = Number(citation.dataset.citationIndex) - 1;
  event.preventDefault();
  if (Number.isInteger(index) && state.evidenceList[index]) {
    openEvidence(state.evidenceList[index], index, citation);
    return;
  }
  // Chat RAG citations are local to the rendered answer rather than research
  // evidence records. Keep the user in the conversation and jump to the
  // matching source entry instead of letting the hash router fall back home.
  const body = citation.closest(".chat-msg-body");
  const sourceLinks = body
    ? [...body.querySelectorAll(`.citation-link[data-citation-index=\"${index + 1}\"]`)]
    : [];
  const source = sourceLinks[sourceLinks.length - 1];
  if (source && source !== citation) source.scrollIntoView({ behavior: "smooth", block: "center" });
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

function initRunFilters() {
  const searchInput = $("#run-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      runSearchQuery = e.target.value.trim();
      renderRuns();
    });
  }
  document.querySelectorAll(".sidebar-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".sidebar-pill").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      runFilter = pill.dataset.filter || "all";
      renderRuns();
    });
  });
}

initTheme();
initRunFilters();
checkHealth().catch((error) => showToast(error.message));
initRouter();
