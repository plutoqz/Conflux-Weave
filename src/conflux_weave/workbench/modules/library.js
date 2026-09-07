import { api, showToast } from "./shared.js";
import { registerView } from "./router.js";

const list = document.getElementById("library-list");
const empty = document.getElementById("library-empty");
const fileInput = document.getElementById("library-file");
const searchInput = document.getElementById("library-search");
const statusSelect = document.getElementById("library-status");
const resultSummary = document.getElementById("library-result-summary");
const paperQuery = document.getElementById("paper-search-query");
const paperSubmit = document.getElementById("paper-search-submit");
const paperResults = document.getElementById("paper-results");
const paperStatus = document.getElementById("paper-search-status");
const paperUnderstanding = document.getElementById("paper-query-understanding");
const paperSourcesStatus = document.getElementById("paper-source-details");
const paperLoadMore = document.getElementById("paper-load-more");
const loadMoreButton = document.getElementById("library-load-more");
let documentOffset = 0;
let documentTotal = 0;
let searchTimer = null;
let paperItems = [];
let paperSourceStates = [];
let paperRequestedLimit = 20;

const STATUS_LABELS = {
  imported: "清单索引可用", parsed: "已解析，待索引", indexing: "正在建立索引",
  knowledge_ready: "知识库可用", index_failed: "索引失败",
  metadata_saved: "仅元数据", oa_unavailable: "无开放全文",
  fetching: "正在获取", fetch_queued: "等待获取", parsing: "正在解析",
  fetch_failed: "获取失败", parse_failed: "解析失败", processing: "处理中",
  failed: "处理失败", pending: "等待处理",
};
const STATUS_GROUPS = {
  ready: ["imported", "knowledge_ready"],
  metadata: ["metadata_saved", "oa_unavailable", "parsed"],
  processing: ["pending", "processing", "fetch_queued", "fetching", "parsing", "indexing"],
  failed: ["failed", "fetch_failed", "parse_failed", "index_failed"],
};

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return bytes ? `${bytes} B` : "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderSummary(payload) {
  document.getElementById("library-total").textContent = String(payload.total || 0);
  document.getElementById("library-imported").textContent = String(payload.imported || 0);
  document.getElementById("library-index-status").textContent = payload.index_status || "未记录";
  document.getElementById("library-characters").textContent = Number(payload.character_count || 0).toLocaleString();
  document.getElementById("library-size").textContent = formatBytes(payload.size_bytes);
  document.getElementById("library-sources").textContent = Object.entries(payload.source_counts || {}).map(([key, value]) => `${key} ${value}`).join(" · ") || "暂无";
  document.getElementById("library-manifest").textContent = payload.manifest || "";
}

function statusTone(status) {
  if (STATUS_GROUPS.failed.includes(status)) return "failed";
  if (STATUS_GROUPS.processing.includes(status)) return "processing";
  if (STATUS_GROUPS.metadata.includes(status)) return "metadata";
  return "ready";
}

function documentCard(item) {
  const article = document.createElement("article");
  article.className = "library-item library-document-item";
  article.tabIndex = 0;
  const main = document.createElement("div");
  main.className = "library-item-main";
  const heading = document.createElement("h3");
  heading.textContent = item.title || item.relative_path || "未命名资料";
  const context = document.createElement("p");
  const authors = (item.authors || []).slice(0, 3).join("、");
  context.textContent = [item.year, authors, item.source_type || item.media_type].filter(Boolean).join(" · ") || item.media_type || "文档";
  const tags = document.createElement("div");
  tags.className = "library-item-tags";
  const status = document.createElement("span");
  status.className = `status-chip status-${statusTone(item.status)}`;
  status.textContent = STATUS_LABELS[item.status] || item.status || "未知状态";
  const type = document.createElement("span");
  type.textContent = item.media_type || "文档";
  tags.append(status, type);
  if (item.doi || item.arxiv_id) {
    const identity = document.createElement("span");
    identity.textContent = item.doi ? `DOI ${item.doi}` : `arXiv ${item.arxiv_id}`;
    tags.append(identity);
  }
  if (item.match_kind === "content") {
    const match = document.createElement("span");
    match.className = "content-match-label";
    match.textContent = "正文命中";
    tags.append(match);
  }
  main.append(heading, context, tags);
  if (item.match_snippet) {
    const snippet = document.createElement("p");
    snippet.className = "library-match-snippet";
    snippet.textContent = `…${item.match_snippet}…`;
    main.append(snippet);
  }
  const metrics = document.createElement("div");
  metrics.className = "library-item-metrics";
  const segments = document.createElement("strong");
  segments.textContent = Number(item.segment_count || 0).toLocaleString();
  const segmentLabel = document.createElement("span");
  segmentLabel.textContent = "正文片段";
  const size = document.createElement("small");
  size.textContent = formatBytes(item.size_bytes);
  metrics.append(segments, segmentLabel, size);
  if (["parsed", "index_failed"].includes(item.status) && item.document_id) {
    const indexButton = document.createElement("button");
    indexButton.className = "quiet-button library-index-button";
    indexButton.type = "button";
    indexButton.textContent = item.status === "index_failed" ? "重试索引" : "加入知识库";
    indexButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      indexButton.disabled = true;
      indexButton.textContent = "索引中…";
      try {
        await api(`/api/v1/library/documents/${encodeURIComponent(item.document_id)}/index`, { method: "POST" });
        await load();
        showToast("资料已加入知识库。");
      } catch (error) {
        indexButton.disabled = false;
        indexButton.textContent = "重试索引";
        showToast(error.message);
      }
    });
    metrics.append(indexButton);
  }
  article.append(main, metrics);
  article.addEventListener("click", () => openDetail(item));
  article.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openDetail(item);
    }
  });
  return article;
}

async function loadDocuments({ append = false } = {}) {
  if (!append) documentOffset = 0;
  const params = new URLSearchParams({ q: searchInput.value.trim(), status: statusSelect.value, offset: String(documentOffset), limit: "30" });
  resultSummary.textContent = "正在检索资料…";
  try {
    const payload = await api(`/api/v1/library/search?${params}`);
    documentTotal = payload.total || 0;
    const cards = (payload.items || []).map(documentCard);
    if (append) list.append(...cards); else list.replaceChildren(...cards);
    documentOffset += cards.length;
    empty.hidden = documentTotal > 0;
    loadMoreButton.hidden = !payload.has_more;
    loadMoreButton.textContent = `加载更多（还有 ${Math.max(0, documentTotal - documentOffset)} 份）`;
    empty.querySelector("strong").textContent = "没有匹配的资料";
    empty.querySelector("span").textContent = "调整关键词或状态筛选后重试。";
    resultSummary.textContent = `匹配 ${documentTotal} 份，当前显示 ${documentOffset} 份`;
  } catch (error) {
    resultSummary.textContent = error.message;
    showToast(error.message);
  }
}

async function openDetail(item) {
  const dialog = document.getElementById("library-detail-dialog");
  document.getElementById("library-detail-title").textContent = item.title || item.relative_path;
  const meta = document.getElementById("library-detail-meta");
  meta.replaceChildren();
  [["来源", item.source], ["类型", item.media_type], ["状态", STATUS_LABELS[item.status] || item.status], ["片段", String(item.segment_count || 0)], ["字符", Number(item.character_count || 0).toLocaleString()], ["大小", formatBytes(item.size_bytes)], ["失败原因", item.error]].forEach(([label, value]) => {
    if (!value && label === "失败原因") return;
    const row = document.createElement("div");
    row.innerHTML = `<dt>${label}</dt><dd></dd>`;
    row.querySelector("dd").textContent = value || "未记录";
    meta.append(row);
  });
  const content = document.getElementById("library-detail-content");
  content.textContent = "正在读取解析内容…";
  dialog.showModal();
  try {
    const detail = await api(`/api/v1/library/documents/${encodeURIComponent(item.record_id || item.document_id)}`);
    const attempts = (item.fetch_attempts || []).map((attempt) => `${attempt.source} · ${attempt.url}\n${attempt.error}`).join("\n\n");
    content.textContent = (detail.segments || []).map((segment) => `${segment.heading || segment.locator?.heading || "文档片段"}\n${segment.text}`).join("\n\n") || attempts || "暂无可预览内容。";
  } catch (error) {
    content.textContent = error.message;
  }
}

async function load() {
  try {
    renderSummary(await api("/api/v1/library"));
    await loadDocuments();
  } catch (error) {
    showToast(error.message);
  }
}

function setLibraryTab(name) {
  document.querySelectorAll("[data-library-tab]").forEach((item) => {
    const selected = item.dataset.libraryTab === name;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-selected", String(selected));
  });
  document.getElementById("library-documents-panel").hidden = name !== "documents";
  document.getElementById("library-papers-panel").hidden = name !== "papers";
  if (name === "documents") loadDocuments();
  else resultSummary.textContent = "跨 OpenAlex 与 arXiv 检索，确认后保存到资料库";
}

function paperIdentity(paper) {
  return paper.doi || (paper.arxiv_id || "").replace(/v\d+$/i, "") || paper.openalex_id || paper.paper_id;
}

function paperCard(paper) {
  const article = document.createElement("article");
  article.className = "library-item paper-result-item";
  const head = document.createElement("div");
  head.className = "paper-result-head";
  const title = document.createElement("h3");
  title.textContent = paper.title;
  const access = document.createElement("span");
  access.className = `status-chip status-${paper.is_oa ? "ready" : "metadata"}`;
  access.textContent = paper.is_oa ? "开放全文候选" : "仅元数据";
  head.append(title, access);
  const meta = document.createElement("p");
  meta.textContent = `${paper.year || "年份未知"} · ${paper.venue || "出版物未知"} · ${(paper.authors || []).slice(0, 4).join("、") || "作者未知"}`;
  const identity = document.createElement("small");
  identity.textContent = `${(paper.sources || []).join(" + ")} · ${paper.doi ? `DOI ${paper.doi}` : paper.arxiv_id ? `arXiv ${paper.arxiv_id}` : paper.openalex_id || "无外部标识"}`;
  const matched = document.createElement("small");
  matched.textContent = (paper.matched_terms || []).length ? `匹配概念：${paper.matched_terms.join("、")}` : "精确标识符匹配";
  const summary = document.createElement("p");
  summary.className = "paper-summary";
  summary.textContent = paper.summary || "暂无摘要。";
  const actions = document.createElement("div");
  actions.className = "paper-actions";
  const metadataButton = document.createElement("button");
  metadataButton.className = "quiet-button";
  metadataButton.type = "button";
  metadataButton.textContent = "保存元数据";
  const fulltextButton = document.createElement("button");
  fulltextButton.className = "primary-button";
  fulltextButton.type = "button";
  fulltextButton.textContent = "获取全文并加入知识库";
  const save = async (action, button) => {
    button.disabled = true;
    button.textContent = action === "metadata" ? "保存中…" : "获取并索引中…";
    try {
      const response = await api("/api/v1/library/papers", { method: "POST", body: JSON.stringify({ action, paper }) });
      await load();
      showToast(response.message || (action === "metadata" ? "论文元数据已保存。" : response.status === "knowledge_ready" ? "论文已加入知识库。" : "论文已解析，等待索引服务。"));
      button.textContent = action === "metadata" ? "已保存" : response.status === "knowledge_ready" ? "知识库可用" : "已解析";
    } catch (error) {
      showToast(error.message);
      button.disabled = false;
      button.textContent = action === "metadata" ? "保存元数据" : "重新获取并索引";
    }
  };
  metadataButton.addEventListener("click", () => save("metadata", metadataButton));
  fulltextButton.addEventListener("click", () => save("fulltext", fulltextButton));
  actions.append(metadataButton, fulltextButton);
  const sourceUrl = (paper.landing_urls || [])[0];
  if (sourceUrl) {
    const link = document.createElement("a");
    link.className = "quiet-button";
    link.href = sourceUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "打开来源";
    actions.append(link);
  }
  article.append(head, meta, identity, matched, summary, actions);
  return article;
}

function renderPaperState(result, retriedSource = null) {
  const understanding = result.query_understanding || {};
  const queries = understanding.queries || [];
  paperUnderstanding.hidden = !queries.length || understanding.status === "direct";
  paperUnderstanding.replaceChildren();
  if (queries.length) {
    const label = document.createElement("strong");
    label.textContent = "检索意图";
    paperUnderstanding.append(label, ...queries.map((query) => {
      const button = document.createElement("button");
      button.className = "paper-intent-button";
      button.type = "button";
      button.textContent = query;
      button.title = "使用此意图重新检索";
      button.addEventListener("click", () => {
        paperQuery.value = query;
        runPaperSearch();
      });
      return button;
    }));
  }
  paperSourceStates = retriedSource ? [...paperSourceStates.filter((item) => item.source !== retriedSource), ...(result.sources || [])] : (result.sources || []);
  paperSourcesStatus.replaceChildren(...paperSourceStates.map((source) => {
    const row = document.createElement("div");
    row.className = `paper-source-row source-${source.status}`;
    const text = document.createElement("div");
    const name = source.source === "openalex" ? "OpenAlex" : "arXiv";
    const state = source.status === "success" ? `${source.count} 条` : source.status === "partial" ? `${source.count} 条，部分失败` : source.status === "no_results" ? "无结果" : "请求失败";
    text.innerHTML = `<strong>${name}</strong><span></span>`;
    text.querySelector("span").textContent = source.message ? `${state} · ${source.message}` : state;
    row.append(text);
    if (["failed", "partial"].includes(source.status)) {
      const retry = document.createElement("button");
      retry.className = "quiet-button";
      retry.type = "button";
      retry.textContent = `重试 ${name}`;
      retry.addEventListener("click", () => runPaperSearch(source.source));
      row.append(retry);
    }
    return row;
  }));
  paperResults.replaceChildren(...paperItems.map(paperCard));
  paperStatus.textContent = `${result.status === "partial" ? "部分完成" : "搜索完成"} · 去重筛选后 ${result.deduplicated_count || paperItems.length} 条，当前显示 ${paperItems.length} 条`;
  paperLoadMore.hidden = paperItems.length < paperRequestedLimit || paperRequestedLimit >= 50;
  paperLoadMore.textContent = `继续获取（最多 ${Math.min(50, paperRequestedLimit + 20)} 条）`;
}

async function runPaperSearch(sourceOverride = null) {
  const query = paperQuery.value.trim();
  if (!query) return showToast("请输入论文搜索关键词。");
  paperSubmit.disabled = true;
  paperSubmit.textContent = "搜索中…";
  paperStatus.textContent = sourceOverride ? `正在重试 ${sourceOverride === "openalex" ? "OpenAlex" : "arXiv"}…` : "正在理解主题并查询 OpenAlex 与 arXiv…";
  if (!sourceOverride) {
    paperItems = [];
    paperResults.replaceChildren();
    paperSourcesStatus.replaceChildren();
  }
  try {
    const params = new URLSearchParams({ query, max_results: String(paperRequestedLimit), sources: sourceOverride || document.getElementById("paper-sources").value, sort: document.getElementById("paper-sort").value });
    const yearFrom = document.getElementById("paper-year-from").value;
    const yearTo = document.getElementById("paper-year-to").value;
    if (yearFrom) params.set("year_from", yearFrom);
    if (yearTo) params.set("year_to", yearTo);
    if (document.getElementById("paper-oa-only").checked) params.set("oa_only", "true");
    const result = await api(`/api/v1/library/papers/search?${params}`, { method: "POST" });
    const combined = sourceOverride ? [...paperItems, ...(result.items || [])] : (result.items || []);
    paperItems = [...new Map(combined.map((paper) => [paperIdentity(paper), paper])).values()];
    renderPaperState(result, sourceOverride);
    if (!paperItems.length) paperStatus.textContent += " · 没有找到符合条件的论文";
  } catch (error) {
    paperStatus.textContent = error.message;
    showToast(error.message);
  } finally {
    paperSubmit.disabled = false;
    paperSubmit.textContent = "搜索论文";
  }
}

document.getElementById("library-refresh").addEventListener("click", load);
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadDocuments(), 250);
});
statusSelect.addEventListener("change", () => loadDocuments());
loadMoreButton.addEventListener("click", () => loadDocuments({ append: true }));
document.getElementById("library-detail-close").addEventListener("click", () => document.getElementById("library-detail-dialog").close());
document.querySelectorAll("[data-library-tab]").forEach((tab) => tab.addEventListener("click", () => setLibraryTab(tab.dataset.libraryTab)));
paperSubmit.addEventListener("click", () => {
  paperRequestedLimit = Number(document.getElementById("paper-result-limit").value || 20);
  runPaperSearch();
});
paperLoadMore.addEventListener("click", () => {
  paperRequestedLimit = Math.min(50, paperRequestedLimit + 20);
  runPaperSearch();
});
paperQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter") paperSubmit.click();
});
fileInput.addEventListener("change", async () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  try {
    const response = await fetch(`/api/v1/library/documents?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "资料导入失败。");
    await load();
    showToast(payload.status === "knowledge_ready" ? "资料已加入知识库。" : "资料已解析，等待索引服务。");
  } catch (error) {
    showToast(error.message);
  }
  fileInput.value = "";
});

registerView("library", { mount: load });
