/* DocumentAgent 权威笔记与持续修订交互模块 (P2 Note Studio)
   纯本地、零外部 CDN 依赖，支持双模视图、版本切换、Patch 持续演进与版本冲突防御。 */

import { $, api, formatDate, showToast } from "./shared.js";

let currentNote = null;
let currentRevisions = [];

const analyzeDialog = document.getElementById("document-analyze-dialog");
const analyzeForm = document.getElementById("document-analyze-form");
const analyzeDocName = document.getElementById("analyze-doc-name");
const analyzeDocId = document.getElementById("analyze-doc-id");
const analyzeDocPath = document.getElementById("analyze-doc-path");
const analyzeFocus = document.getElementById("analyze-focus-input");
const analyzeTitle = document.getElementById("analyze-custom-title");
const analyzeSubmitBtn = document.getElementById("analyze-submit-btn");

const noteDialog = document.getElementById("document-note-dialog");
const noteCloseBtn = document.getElementById("note-studio-close");
const noteTitle = document.getElementById("note-studio-title");
const noteVersionBadge = document.getElementById("note-studio-version");
const noteDocBadge = document.getElementById("note-studio-doc-badge");
const noteTime = document.getElementById("note-studio-time");
const noteRevisionsSelect = document.getElementById("note-studio-revisions");
const noteTabHtml = document.getElementById("note-tab-html");
const noteTabMd = document.getElementById("note-tab-md");
const noteContentHtml = document.getElementById("note-content-html");
const noteContentMd = document.getElementById("note-content-md");
const noteHtmlFrame = document.getElementById("note-html-frame");
const noteMdView = document.getElementById("note-md-view");
const noteCopyBtn = document.getElementById("note-copy-md-btn");
const notePopoutBtn = document.getElementById("note-popout-btn");

const notePatchForm = document.getElementById("note-patch-form");
const notePatchInput = document.getElementById("note-patch-input");
const notePatchSubmit = document.getElementById("note-patch-submit");
const notePatchStatus = document.getElementById("note-patch-status");

export function openAnalyzeDialog(item) {
  if (!analyzeDialog) return;
  if (!item) {
    showToast("无法识别待研读的文档。");
    return;
  }
  if (item.status === "metadata_saved" || item.status === "oa_unavailable" || item.status === "fetch_failed") {
    showToast("该文献目前仅有元数据，尚未获取到全文内容，请先在资料库点击“获取全文并加入知识库”。");
    return;
  }
  const name = item.title || item.relative_path || "未命名文档";
  analyzeDocName.value = name;
  analyzeDocId.value = item.document_id || item.record_id || "";
  analyzeDocPath.value = item.relative_path || item.path || "";
  analyzeFocus.value = "";
  analyzeTitle.value = "";
  analyzeSubmitBtn.disabled = false;
  analyzeSubmitBtn.querySelector("span").textContent = "开始智能研读";
  analyzeDialog.showModal();
}

async function runAnalyzeDocument(docId, docPath, focus, customTitle) {
  if (!docId && !docPath) {
    showToast("请指定待研读的文档或路径。");
    return;
  }
  analyzeSubmitBtn.disabled = true;
  analyzeSubmitBtn.querySelector("span").textContent = "DocumentAgent 研读中…";

  try {
    const payload = {};
    if (docId) payload.document_id = docId;
    if (docPath) payload.path = docPath;
    if (focus) payload.focus = focus;
    if (customTitle) payload.title = customTitle;

    const note = await api("/api/v1/documents/analyze", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    analyzeDialog.close();
    showToast(`文档研读完成，已生成权威笔记 v${note.version}。`);
    await openNoteStudio(note);
  } catch (error) {
    analyzeSubmitBtn.disabled = false;
    analyzeSubmitBtn.querySelector("span").textContent = "开始智能研读";
    showToast(error.message || "文档研读失败，请检查文件格式。");
  }
}

export async function openNoteStudio(noteData) {
  if (!noteDialog) return;
  currentNote = noteData;

  noteTitle.textContent = noteData.title || "文档研读笔记";
  noteVersionBadge.textContent = `v${noteData.version}`;
  noteDocBadge.textContent = noteData.metadata?.media_type || "Document";
  noteTime.textContent = `生成时间: ${noteData.created_at ? formatDate(noteData.created_at) : "刚刚"}`;

  // Render HTML in isolated sandbox iframe
  if (noteHtmlFrame) {
    noteHtmlFrame.srcdoc = noteData.html_content || "<p>无内容</p>";
  }
  // Render Raw Markdown
  if (noteMdView) {
    noteMdView.textContent = noteData.markdown_content || "";
  }

  // Set view tab to HTML by default
  setViewMode("html");

  // Reset patch inputs
  if (notePatchInput) notePatchInput.value = "";
  if (notePatchSubmit) {
    notePatchSubmit.disabled = false;
    notePatchSubmit.querySelector("span").textContent = "应用修订";
  }
  if (notePatchStatus) notePatchStatus.hidden = true;

  // Fetch revisions lineage
  await loadRevisions(noteData.note_id, noteData.version);

  if (!noteDialog.open) {
    noteDialog.showModal();
  }
}

export async function openNoteById(noteId) {
  try {
    showToast("正在加载权威笔记…");
    const note = await api(`/api/v1/notes/${encodeURIComponent(noteId)}`);
    await openNoteStudio(note);
  } catch (error) {
    showToast(error.message || "未能加载指定笔记。");
  }
}

async function loadRevisions(noteId, currentVersion) {
  if (!noteRevisionsSelect) return;
  noteRevisionsSelect.replaceChildren();

  try {
    const res = await api(`/api/v1/notes/${encodeURIComponent(noteId)}/revisions`);
    currentRevisions = res.revisions || [];
    if (!currentRevisions.length) {
      const opt = document.createElement("option");
      opt.value = noteId;
      opt.textContent = `v${currentVersion} (当前版本)`;
      noteRevisionsSelect.append(opt);
      return;
    }

    currentRevisions.forEach((rev) => {
      const opt = document.createElement("option");
      opt.value = rev.note_id;
      const isCurrent = rev.version === currentVersion;
      const instr = rev.instruction ? ` - ${rev.instruction.slice(0, 16)}…` : "";
      opt.textContent = `v${rev.version}${isCurrent ? " (当前)" : ""}${instr}`;
      if (isCurrent) opt.selected = true;
      noteRevisionsSelect.append(opt);
    });
  } catch {
    const fallbackOpt = document.createElement("option");
    fallbackOpt.value = noteId;
    fallbackOpt.textContent = `v${currentVersion}`;
    noteRevisionsSelect.append(fallbackOpt);
  }
}

function setViewMode(mode) {
  const isHtml = mode === "html";
  if (noteTabHtml) {
    noteTabHtml.classList.toggle("active", isHtml);
    noteTabHtml.setAttribute("aria-selected", String(isHtml));
  }
  if (noteTabMd) {
    noteTabMd.classList.toggle("active", !isHtml);
    noteTabMd.setAttribute("aria-selected", String(!isHtml));
  }
  if (noteContentHtml) noteContentHtml.hidden = !isHtml;
  if (noteContentMd) noteContentMd.hidden = isHtml;
}

async function submitPatchRevision(event) {
  event?.preventDefault();
  if (!currentNote) return;

  const instruction = notePatchInput.value.trim();
  if (!instruction) {
    showToast("请输入修订意图指令。");
    notePatchInput.focus();
    return;
  }

  notePatchSubmit.disabled = true;
  notePatchSubmit.querySelector("span").textContent = "DocumentAgent 修订中…";
  if (notePatchStatus) {
    notePatchStatus.hidden = false;
    notePatchStatus.textContent = "正在规划原子操作并应用 Patch…";
  }

  try {
    const response = await fetch(`/api/v1/notes/${encodeURIComponent(currentNote.note_id)}/patch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instruction,
        target_version: currentNote.version,
      }),
    });

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      showToast(conflict.message || "检测到版本并发冲突，已自动为您载入最新版本。");
      // Reload latest revision
      if (currentRevisions.length) {
        const latest = currentRevisions[currentRevisions.length - 1];
        await openNoteById(latest.note_id);
      }
      return;
    }

    if (!response.ok) {
      const err = await response.json().catch(() => ({ message: "修订请求失败" }));
      throw new Error(err.message || `请求失败 (${response.status})`);
    }

    const newNote = await response.json();
    showToast(`修订成功！已演进为版本 v${newNote.version}。`);
    await openNoteStudio(newNote);
  } catch (error) {
    showToast(error.message || "应用修订失败。");
  } finally {
    notePatchSubmit.disabled = false;
    notePatchSubmit.querySelector("span").textContent = "应用修订";
    if (notePatchStatus) notePatchStatus.hidden = true;
  }
}

function copyMarkdown() {
  if (!currentNote || !currentNote.markdown_content) {
    showToast("当前没有可复制的 Markdown 内容。");
    return;
  }
  navigator.clipboard.writeText(currentNote.markdown_content).then(
    () => showToast("已成功复制 Markdown 权威笔记。"),
    () => showToast("复制到剪贴板失败，请手动选择复制。")
  );
}

function popoutHtml() {
  if (!currentNote || !currentNote.html_content) {
    showToast("当前没有可导出的 HTML 内容。");
    return;
  }
  const blob = new Blob([currentNote.html_content], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
}

export function initNotes() {
  // Analyze dialog events
  if (analyzeForm) {
    analyzeForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (e.submitter && (e.submitter.id === "analyze-dialog-close" || e.submitter.id === "analyze-cancel-btn")) {
        analyzeDialog.close();
        return;
      }
      runAnalyzeDocument(
        analyzeDocId.value,
        analyzeDocPath.value,
        analyzeFocus.value.trim(),
        analyzeTitle.value.trim()
      );
    });
  }
  const dialogCloseBtn = document.getElementById("analyze-dialog-close");
  if (dialogCloseBtn && analyzeDialog) {
    dialogCloseBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      analyzeDialog.close();
    });
  }
  const cancelBtn = document.getElementById("analyze-cancel-btn");
  if (cancelBtn && analyzeDialog) {
    cancelBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      analyzeDialog.close();
    });
  }

  // Backdrop click auto-close support
  if (analyzeDialog) {
    analyzeDialog.addEventListener("click", (e) => {
      if (e.target === analyzeDialog) analyzeDialog.close();
    });
  }
  if (noteDialog) {
    noteDialog.addEventListener("click", (e) => {
      if (e.target === noteDialog) noteDialog.close();
    });
  }

  // Note studio header events
  if (noteCloseBtn && noteDialog) {
    noteCloseBtn.addEventListener("click", () => noteDialog.close());
  }
  if (noteTabHtml) {
    noteTabHtml.addEventListener("click", () => setViewMode("html"));
  }
  if (noteTabMd) {
    noteTabMd.addEventListener("click", () => setViewMode("md"));
  }
  if (noteCopyBtn) {
    noteCopyBtn.addEventListener("click", copyMarkdown);
  }
  if (notePopoutBtn) {
    notePopoutBtn.addEventListener("click", popoutHtml);
  }

  // Revision selector switch
  if (noteRevisionsSelect) {
    noteRevisionsSelect.addEventListener("change", (e) => {
      const targetId = e.target.value;
      if (targetId && targetId !== currentNote?.note_id) {
        openNoteById(targetId);
      }
    });
  }

  // Patch form submission
  if (notePatchForm) {
    notePatchForm.addEventListener("submit", submitPatchRevision);
  }

  // Expose global helper for note studio access
  window.openNoteById = openNoteById;
  window.openNoteStudio = openNoteStudio;
}

// Auto initialize when module is loaded
if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initNotes);
  } else {
    initNotes();
  }
}
