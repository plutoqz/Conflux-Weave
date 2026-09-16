import React, { useState, useEffect, useMemo, useRef } from "react";
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { FileText, Copy, Sparkles, Check, History, Loader2, AlertCircle, Download, BookOpen, Quote } from "lucide-react";
import { marked } from "marked";
import { renderMarkdownWithMath, cleanDocumentNoteText } from "@/lib/math";
import { api } from "@/services/api";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { cn } from "@/lib/utils";
import type { DocumentNote } from "@/types/workbench";

interface SegmentRow {
  segment_id: string;
  ordinal: number;
  text: string;
  locator: Record<string, any>;
}

interface QuoteAnchor {
  document_id: string;
  quote: string;
  page?: number;
  segment_id?: string;
  asset_id?: string;
  unanchored?: boolean;
}

interface NoteStudioDialogProps {
  documentId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const NoteStudioDialog: React.FC<NoteStudioDialogProps> = ({
  documentId,
  open,
  onOpenChange,
}) => {
  const { addBackgroundTask, updateBackgroundTask } = useWorkbenchStore();
  const [note, setNote] = useState<DocumentNote | null>(null);
  const [activeTab, setActiveTab] = useState<"html" | "md" | "source">("html");
  const [patchPrompt, setPatchPrompt] = useState("");
  const [patching, setPatching] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflictMsg, setConflictMsg] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revisions, setRevisions] = useState<any[]>([]);
  const [savingToResearch, setSavingToResearch] = useState(false);
  const [savedResearchRunId, setSavedResearchRunId] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string>("");
  // A2 研读联动：原文分段与选择引用锚点
  const [segments, setSegments] = useState<SegmentRow[]>([]);
  const [anchor, setAnchor] = useState<QuoteAnchor | null>(null);
  const [anchorNote, setAnchorNote] = useState<string | null>(null);
  const readerRef = useRef<HTMLDivElement | null>(null);

  const handleExportNote = async (format: "markdown" | "json") => {
    if (!note?.note_id || exporting) return;
    setExporting(format);
    try {
      await api.exportNote(note.note_id, format);
    } catch (err: any) {
      alert(`导出笔记失败: ${err.message || err}`);
    } finally {
      setExporting("");
    }
  };

  useEffect(() => {
    if (!open || !documentId) {
      setNote(null);
      setError(null);
      setConflictMsg(null);
      setRevisions([]);
      setSavedResearchRunId(null);
      setSegments([]);
      setAnchor(null);
      setAnchorNote(null);
      return;
    }
    setLoading(true);
    setError(null);
    setConflictMsg(null);
    setSavedResearchRunId(null);
    setAnchor(null);
    setAnchorNote(null);

    // A2：加载原文分段（页码定位），供阅读窗格与选择引用使用
    api.getDocumentSegments(documentId).then((res) => setSegments(res.segments || [])).catch(() => setSegments([]));

    const taskId = `task-reading-${documentId}`;
    addBackgroundTask({
      id: taskId,
      title: `研读分析: ${documentId}`,
      type: "reading",
      status: "running",
      startTime: Date.now(),
      targetId: documentId,
    });

    // Call analyzeDocument (which gets existing or builds new)
    api.analyzeDocument(documentId)
      .then(async (res) => {
        setNote(res);
        updateBackgroundTask(taskId, {
          status: "succeeded",
          message: `研读完成：《${res.title}》`,
        });
        if (res.note_id) {
          try {
            const revRes = await api.getDocumentNoteRevisions(res.note_id);
            setRevisions(revRes.revisions || []);
          } catch {
            // ignore
          }
        }
      })
      .catch((err) => {
        console.error("Failed to analyze document:", err);
        setError(err.message || "文档研读失败，请检查文件是否存在全文。");
        updateBackgroundTask(taskId, {
          status: "failed",
          message: err.message || "研读失败",
        });
      })
      .finally(() => setLoading(false));
  }, [open, documentId]);

  const handleSelectRevision = async (noteId: string) => {
    if (!noteId) return;
    try {
      setLoading(true);
      const revNote = await api.getDocumentNote(noteId);
      setNote(revNote);
    } catch (err: any) {
      alert(`无法加载指定版本: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleApplyPatch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!patchPrompt.trim() || !note) return;

    setPatching(true);
    setConflictMsg(null);
    const finish = (updated: DocumentNote) => {
      setNote(updated);
      setPatchPrompt("");
      // Refresh revisions
      return api.getDocumentNoteRevisions(updated.note_id).then((revRes) => setRevisions(revRes.revisions || [])).catch(() => {});
    };

    try {
      await finish(await api.patchDocumentNote(note.note_id, patchPrompt.trim(), note.version, anchor ?? undefined));
      setAnchor(null); // 锚点随修订成功持久化进新版本；失败时保留供重试
      setAnchorNote(null);
    } catch (err: any) {
      // A5：409 版本冲突 → 基线自动刷新并重应用一次；再次冲突转人工，不循环重试。
      if (err?.status === 409) {
        const latest: number | null =
          typeof err?.payload?.latest_version === "number"
            ? err.payload.latest_version
            : await api
                .getDocumentNote(note.note_id)
                .then((n) => (typeof n?.version === "number" ? n.version : null))
                .catch(() => null);
        if (latest != null && latest !== note.version) {
          setConflictMsg(`检测到版本冲突（目标 v${note.version}，服务端已到 v${latest}），已基于最新版本自动重应用一次…`);
          try {
            await finish(await api.patchDocumentNote(note.note_id, patchPrompt.trim(), latest, anchor ?? undefined));
            setAnchor(null);
            setAnchorNote(null);
          } catch (retryErr: any) {
            setConflictMsg(
              retryErr?.status === 409
                ? `自动重应用后仍冲突（服务端已到更高版本），请刷新笔记后手动重试。`
                : `自动重应用失败: ${retryErr?.message || retryErr}`
            );
          }
        } else {
          setConflictMsg("检测到版本冲突且无法确定最新版本，请关闭后重新打开笔记再试。");
        }
      } else {
        alert(`修订失败: ${err?.message || err}`);
      }
    } finally {
      setPatching(false);
    }
  };

  const handleSaveToResearch = async () => {
    if (!note?.note_id) return;
    try {
      setSavingToResearch(true);
      const res = await api.saveNoteToResearch(note.note_id);
      setSavedResearchRunId(res.run_id);
      useWorkbenchStore.getState().refreshRuns();
    } catch (err: any) {
      alert(`保存报告到研究失败: ${err.message || err}`);
    } finally {
      setSavingToResearch(false);
    }
  };

  const rawHtml = note?.html_content || note?.content_html || "";
  const rawMd = note?.markdown_content || note?.content_markdown || "";

  const mdContent = cleanDocumentNoteText(rawMd);

  // A2：分段按页分组（locator.page 缺失时归入"无定位"组并明确标记）
  const segmentsByPage = useMemo(() => {
    const groups = new Map<number | "none", SegmentRow[]>();
    for (const seg of segments) {
      const page = typeof seg.locator?.page === "number" ? (seg.locator.page as number) : "none";
      const list = groups.get(page) || [];
      list.push(seg);
      groups.set(page, list);
    }
    return [...groups.entries()].sort((a, b) => {
      if (a[0] === "none") return 1;
      if (b[0] === "none") return -1;
      return (a[0] as number) - (b[0] as number);
    });
  }, [segments]);

  // A2：从阅读窗格的文本选择构造引用锚点；无定位信息时显式 unanchored
  const captureSelectionAnchor = () => {
    const selection = window.getSelection();
    const text = selection?.toString().trim() || "";
    if (!text || !documentId) return;
    const node = selection?.anchorNode;
    const element = (node instanceof Element ? node : node?.parentElement) || null;
    const segmentEl = element?.closest("[data-segment-id]") as HTMLElement | null;
    if (segmentEl) {
      const segmentId = segmentEl.dataset.segmentId || "";
      const pageRaw = segmentEl.dataset.page;
      const page = pageRaw != null && pageRaw !== "" ? Number(pageRaw) : undefined;
      setAnchor({
        document_id: documentId,
        quote: text,
        segment_id: segmentId || undefined,
        page: Number.isFinite(page as number) ? (page as number) : undefined,
      });
    } else {
      setAnchor({ document_id: documentId, quote: text, unanchored: true });
    }
    setAnchorNote(null);
  };

  const jumpToAnchor = (target: QuoteAnchor | null) => {
    const segmentId = target?.segment_id;
    if (!segmentId) return;
    setActiveTab("source");
    requestAnimationFrame(() => {
      const el = readerRef.current?.querySelector(`[data-segment-id="${CSS.escape(segmentId)}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.classList.add("ring-2", "ring-emerald-500", "bg-emerald-500/10");
      setTimeout(() => el?.classList.remove("ring-2", "ring-emerald-500", "bg-emerald-500/10"), 2200);
    });
  };

  // Prepared HTML for iframe: if htmlContent is present, enhance it; otherwise parse mdContent
  let displayHtml = "";
  if (rawHtml) {
    let enhancedHtml = cleanDocumentNoteText(rawHtml);
    if (!enhancedHtml.includes("katex.min.css")) {
      const katexHead = `
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {delimiters: [{left: '$$', right: '$$', display: true}, {left: '\\\\[', right: '\\\\]', display: true}, {left: '$', right: '$', display: false}, {left: '\\\\(', right: '\\\\)', display: false}], throwOnError: false});"></script>
      `;
      if (enhancedHtml.includes("</head>")) {
        enhancedHtml = enhancedHtml.replace("</head>", `${katexHead}</head>`);
      } else {
        enhancedHtml = `${katexHead}${enhancedHtml}`;
      }
    }
    displayHtml = enhancedHtml;
  } else if (mdContent) {
    const renderedBody = renderMarkdownWithMath(mdContent);
    displayHtml = `<!DOCTYPE html><html><head><meta charset="utf-8">
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
      <style>
        body { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; padding: 24px 32px; line-height: 1.85; color: #181816; background: #FAF9F6; margin: 0; }
        h1 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #1B4931; margin-top: 1.6em; border-bottom: 2px solid #1B4931; padding-bottom: 8px; font-size: 1.6rem; font-weight: 700; }
        h2 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #6d28d9; border-left: 4px solid #8b5cf6; padding-left: 10px; margin-top: 1.5em; font-size: 1.3rem; font-weight: 700; }
        h3 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #0369a1; border-left: 3.5px solid #0ea5e9; padding-left: 9px; margin-top: 1.3em; font-size: 1.12rem; font-weight: 650; }
        h4 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #0f766e; border-left: 3px solid #06b6d4; padding-left: 8px; margin-top: 1.2em; font-size: 1.02rem; font-weight: 600; }
        h5 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #15803d; border-left: 3px solid #10b981; padding-left: 8px; margin-top: 1.1em; font-size: 0.96rem; font-weight: 600; }
        h6 { font-family: "江西拙楷", "JiangXiZhuoKai", "Charter", "Source Serif 4", Georgia, serif; color: #be123c; border-left: 3px solid #f43f5e; padding-left: 8px; margin-top: 1.0em; font-size: 0.92rem; font-weight: 600; }
        table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }
        th, td { border: 1px solid #D5D3CC; padding: 8px 12px; text-align: left; }
        th { background: #EAE8E0; font-weight: 600; }
        code { background: #EAE8E0; padding: 2px 6px; border-radius: 4px; font-size: 12px; font-family: monospace; }
        blockquote { border-left: 3px solid #1B4931; margin: 16px 0; padding-left: 16px; color: #555; }
        .katex-display { margin: 16px 0; overflow-x: auto; text-align: center; }
      </style></head><body>${renderedBody}</body></html>`;
  } else {
    displayHtml = "<p style='padding: 24px; color: #888;'>暂无内容</p>";
  }

  const handleCopyMd = () => {
    if (mdContent) {
      navigator.clipboard.writeText(mdContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl w-[94vw] h-[90vh] flex flex-col p-0 gap-0 overflow-hidden bg-background">
        {/* Note Studio Header */}
        <div className="p-4 border-b border-border/70 flex items-center justify-between bg-card/60">
          <div className="flex items-center space-x-3 min-w-0">
            <div className="h-8 w-8 rounded-lg bg-emerald-900/10 text-emerald-800 dark:text-emerald-300 flex items-center justify-center shrink-0">
              <FileText className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center space-x-2">
                <h3 className="text-base font-serif-academic font-bold text-foreground truncate max-w-md">
                  {note?.title || "文档研读笔记"}
                </h3>
                {note && (
                  <Badge variant="outline" className="text-xs font-mono border-emerald-800/40 text-emerald-800 dark:text-emerald-300 bg-emerald-800/5 font-semibold">
                    v{note.version}
                  </Badge>
                )}
                {(() => {
                  const retained = (note as any)?.metadata?.quote_anchor as QuoteAnchor | undefined;
                  if (!retained) return null;
                  return (
                    <button
                      onClick={() => jumpToAnchor(retained)}
                      title={`本版本修订引用了原文：${retained.quote.slice(0, 60)}`}
                      className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-emerald-700/40 text-emerald-800 dark:text-emerald-300 hover:bg-emerald-500/10 max-w-[220px] truncate"
                    >
                      引用原文{retained.unanchored ? "（无定位）" : retained.page != null ? ` · 第 ${retained.page} 页` : ""}
                    </button>
                  );
                })()}
              </div>
              {(() => {
                const noteMeta = (note?.metadata || {}) as Record<string, any>;
                const noteChars = noteMeta.character_count || ((note?.markdown_content || "").length) || (note?.executive_summary || "").length;
                const noteWords = noteMeta.word_count || ((note?.markdown_content || "").match(/[\u4e00-\u9fff]|[a-zA-Z0-9_-]+/g) || []).length;
                const noteTokens = noteMeta.tokens_consumed ?? ((noteMeta.input_tokens ?? 0) + (noteMeta.output_tokens ?? 0));
                const noteElapsed = noteMeta.elapsed_seconds ?? 0;
                return (
                  <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-foreground/75 font-mono mt-0.5">
                    <span className="truncate max-w-[200px]" title={documentId || ""}>Doc: {documentId}</span>
                    {note && (
                      <>
                        <span>·</span>
                        <span className="text-foreground/90 font-medium">
                          字数: {noteChars.toLocaleString()} 字 ({noteWords.toLocaleString()} 词)
                        </span>
                        {noteTokens > 0 && (
                          <>
                            <span>·</span>
                            <span>Token: {noteTokens.toLocaleString()}</span>
                          </>
                        )}
                        {noteElapsed > 0 && (
                          <>
                            <span>·</span>
                            <span>耗时: {noteElapsed}s</span>
                          </>
                        )}
                      </>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* Revisions & View Mode Tabs */}
          <div className="flex items-center space-x-2 shrink-0">
            {revisions.length > 1 && (
              <div className="flex items-center space-x-1 text-xs">
                <History className="h-3.5 w-3.5 text-muted-foreground" />
                <select
                  value={note?.note_id || ""}
                  onChange={(e) => handleSelectRevision(e.target.value)}
                  className="bg-muted text-xs rounded px-2 py-1 border border-border/80 focus:outline-none font-serif-academic"
                >
                  {revisions.map((rev) => (
                    <option key={rev.note_id} value={rev.note_id}>
                      v{rev.version} {rev.instruction ? `(${rev.instruction.slice(0, 10)}...)` : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="flex bg-muted/80 p-0.5 rounded-lg text-xs font-serif-academic">
              <button
                onClick={() => setActiveTab("html")}
                className={cn(
                  "px-2.5 py-1 rounded-md transition font-medium",
                  activeTab === "html"
                    ? "bg-background text-foreground shadow-xs font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                HTML 视图
              </button>
              <button
                onClick={() => setActiveTab("md")}
                className={cn(
                  "px-2.5 py-1 rounded-md transition font-medium",
                  activeTab === "md"
                    ? "bg-background text-foreground shadow-xs font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                Markdown 原文
              </button>
              <button
                onClick={() => setActiveTab("source")}
                className={cn(
                  "px-2.5 py-1 rounded-md transition font-medium gap-1 flex items-center",
                  activeTab === "source"
                    ? "bg-background text-foreground shadow-xs font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                )}
                title="原文分段阅读：选中文本后可引用到笔记（带页码与分段定位）"
              >
                <BookOpen className="h-3 w-3" />
                原文
              </button>
            </div>

            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopyMd}
              disabled={!mdContent}
              title="复制 Markdown 原文"
              className="h-7 px-2 text-xs font-serif-academic gap-1 text-muted-foreground hover:text-foreground"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
              <span>{copied ? "已复制" : "复制"}</span>
            </Button>

            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleExportNote("markdown")}
              disabled={!note || !!exporting}
              title="导出笔记 Markdown 文件"
              className="h-7 px-2 text-xs font-serif-academic gap-1 text-muted-foreground hover:text-foreground"
            >
              <Download className="h-3.5 w-3.5" />
              <span>{exporting === "markdown" ? "导出中…" : "导出 MD"}</span>
            </Button>

            {savedResearchRunId ? (
              <Badge variant="outline" className="text-xs font-serif-academic border-emerald-600 text-emerald-700 bg-emerald-50 dark:bg-emerald-950/40 dark:text-emerald-300 gap-1 py-1 px-2.5">
                <Check className="h-3 w-3 text-emerald-600" />
                <span>已保存到研究 ({savedResearchRunId.slice(0, 12)})</span>
              </Badge>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={handleSaveToResearch}
                disabled={!note || savingToResearch}
                title="将当前研读报告归档并保存到深度研究中心"
                className="h-7 px-2.5 text-xs font-serif-academic gap-1.5 border-emerald-700/50 text-emerald-800 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-950/50"
              >
                {savingToResearch ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                )}
                <span>保存报告到研究</span>
              </Button>
            )}
          </div>
        </div>

        {/* Note Body */}
        <div className="flex-1 overflow-hidden bg-background relative">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-4 p-6">
              <Loader2 className="h-8 w-8 animate-spin text-emerald-800 dark:text-emerald-400" />
              <div className="space-y-1">
                <p className="text-base font-serif-academic font-medium text-foreground">
                  DocumentAgent 正在研读文档...
                </p>
                <p className="text-xs sm:text-sm font-serif-academic text-muted-foreground max-w-sm">
                  正在解析长文档切片、提炼核心概念与推导脉络。研读任务支持全异步执行。
                </p>
              </div>
              <div className="pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onOpenChange(false)}
                  className="font-serif-academic text-xs gap-1.5 border-border hover:border-emerald-700/50"
                >
                  <span>转入后台运行 (关闭弹窗不中断)</span>
                </Button>
              </div>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-3 p-6">
              <AlertCircle className="h-8 w-8 text-rose-500" />
              <div className="space-y-1">
                <p className="text-sm font-serif-academic font-semibold text-foreground">
                  文档研读未能完成
                </p>
                <p className="text-xs sm:text-sm font-serif-academic text-foreground/80 max-w-md">
                  {error}
                </p>
              </div>
            </div>
          ) : !note ? (
            <div className="flex items-center justify-center h-full text-xs sm:text-sm text-foreground/75 font-serif-academic">
              未找到此文档的研读笔记。
            </div>
          ) : activeTab === "html" ? (
            <div className="w-full h-full overflow-y-auto px-6 sm:px-12 py-8 select-text bg-background/50">
              <div
                className="max-w-5xl mx-auto prose prose-stone dark:prose-invert max-w-none font-serif-academic leading-relaxed text-foreground [&_table]:w-full [&_table]:border-collapse [&_table]:my-5 [&_table]:text-xs sm:[&_table]:text-sm [&_th]:border [&_th]:border-border/80 [&_th]:p-3 [&_th]:bg-muted/40 [&_th]:font-semibold [&_th]:break-words [&_td]:border [&_td]:border-border/60 [&_td]:p-3 [&_.katex-display]:my-4 [&_.katex-display]:overflow-x-auto [&_.katex-display]:text-center"
                dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(mdContent || rawHtml) }}
              />
            </div>
          ) : activeTab === "source" ? (
            <div className="w-full h-full flex flex-col">
              <div className="px-4 py-2 border-b border-border/60 bg-muted/30 text-[11px] text-muted-foreground flex items-center justify-between shrink-0">
                <span>
                  原文分段阅读（A2 批次一：定位到「页码 + 分段」，非 PDF 版式渲染）
                  {segments.length === 0 && " · 本文档无可显示分段"}
                </span>
                {anchor && <span className="font-mono">已捕获引用：{anchor.unanchored ? "无定位（unanchored）" : `第 ${anchor.page ?? "?"} 页 / ${anchor.segment_id?.slice(-10)}`}</span>}
              </div>
              <div
                ref={readerRef}
                onMouseUp={captureSelectionAnchor}
                className="flex-1 overflow-auto p-4 space-y-3 select-text"
              >
                {segmentsByPage.map(([page, rows]) => (
                  <div key={String(page)}>
                    <div className="sticky top-0 z-10 bg-background/95 backdrop-blur px-2 py-1 text-[11px] font-mono font-semibold text-emerald-800 dark:text-emerald-300 border-b border-border/40">
                      {page === "none" ? "⚠ 未定位分段（原文本携带页码）" : `第 ${page} 页`}
                    </div>
                    <div className="space-y-2 mt-2">
                      {rows.map((seg) => (
                        <p
                          key={seg.segment_id}
                          data-segment-id={seg.segment_id}
                          data-page={typeof seg.locator?.page === "number" ? seg.locator.page : ""}
                          className="text-xs sm:text-sm leading-relaxed text-foreground/90 rounded-md p-2 transition cursor-text hover:bg-muted/50"
                        >
                          {seg.text}
                        </p>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="p-6 overflow-auto h-full font-mono text-xs sm:text-sm text-foreground whitespace-pre-wrap leading-relaxed bg-muted/10 selection:bg-emerald-800/20">
              {mdContent || "暂无 Markdown 原文"}
            </div>
          )}
        </div>

        {/* Patch Studio Footer */}
        <div className="p-3 border-t border-border/70 bg-card/60 space-y-2">
          {anchor && (
            <div className="flex items-start gap-2 p-2 rounded-md bg-emerald-500/10 border border-emerald-600/30 text-xs">
              <Quote className="h-3.5 w-3.5 shrink-0 mt-0.5 text-emerald-700 dark:text-emerald-400" />
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-foreground/90">“{anchor.quote.slice(0, 180)}{anchor.quote.length > 180 ? "…" : ""}”</p>
                <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {anchor.unanchored
                    ? "⚠ 无定位信息（unanchored，不伪造页码）"
                    : `引用锚点：第 ${anchor.page ?? "?"} 页${anchor.segment_id ? ` · ${anchor.segment_id.slice(0, 26)}` : ""}`}
                </p>
              </div>
              {anchor.segment_id && (
                <Button variant="ghost" size="sm" className="h-6 px-2 text-[10px]" onClick={() => jumpToAnchor(anchor)}>
                  回到原文
                </Button>
              )}
              <Button variant="ghost" size="sm" className="h-6 px-2 text-[10px]" onClick={() => { setAnchor(null); setAnchorNote(null); }}>
                移除
              </Button>
            </div>
          )}
          {anchorNote && (
            <div className="flex items-start gap-2 p-2 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400 text-xs">
              <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>{anchorNote}</span>
            </div>
          )}
          {conflictMsg && (
            <div className="flex items-start gap-2 p-2 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400 text-xs">
              <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>{conflictMsg}</span>
            </div>
          )}
          <div className="flex items-center space-x-2">
            <Sparkles className="h-4 w-4 text-emerald-800 dark:text-emerald-400 shrink-0" />
            <form onSubmit={handleApplyPatch} className="flex-1 flex gap-2">
            <Input
              value={patchPrompt}
              onChange={(e) => setPatchPrompt(e.target.value)}
              placeholder="输入自然语言修订意图（例如：“精炼第三节推导过程”、“增加概念对比表”、“补充核心算法局限性”）..."
              className="text-xs sm:text-sm flex-1 font-serif-academic"
              disabled={patching || !note}
            />
            <Button
              type="submit"
              size="sm"
              disabled={patching || !patchPrompt.trim() || !note}
              className="shrink-0 text-xs font-serif-academic bg-emerald-800 hover:bg-emerald-900 text-white"
            >
              {patching ? "正在演进修订..." : "应用修订"}
            </Button>
          </form>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
