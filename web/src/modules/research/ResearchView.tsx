import React, { useEffect, useState, useMemo } from "react";
import { Marked } from "marked";
import katex from "katex";
import {
  RotateCcw,
  MessageSquarePlus,
  AlertTriangle,
  FileText,
  ListTree,
  ShieldCheck,
  Activity,
  CheckCircle2,
  ExternalLink,
  ChevronRight,
  ChevronDown,
  Download,
  Image as ImageIcon,
} from "lucide-react";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { RunDetail } from "@/types/workbench";

export const ResearchView: React.FC = () => {
  const {
    activeRunId,
    activeRunDetail,
    setActiveRunDetail,
    setIsFollowUpOpen,
  } = useWorkbenchStore();

  const [loading, setLoading] = useState(false);
  const [reportText, setReportText] = useState<string>("");
  const [rightTab, setRightTab] = useState<"toc" | "evidence" | "activity">("toc");
  const [headings, setHeadings] = useState<Array<{ id: string; text: string; level: number }>>([]);
  const [evidenceList, setEvidenceList] = useState<any[]>([]);
  const [totalEvidenceCount, setTotalEvidenceCount] = useState<number>(0);
  const [allEvidenceIds, setAllEvidenceIds] = useState<string[]>([]);
  const [loadingMoreEvidence, setLoadingMoreEvidence] = useState<boolean>(false);
  const [expandedEvidenceIds, setExpandedEvidenceIds] = useState<Set<string>>(new Set());
  const [previewImage, setPreviewImage] = useState<{ url: string; title: string } | null>(null);
  const [exporting, setExporting] = useState<string>("");
  const [exportError, setExportError] = useState<string>("");
  const [runSteps, setRunSteps] = useState<any[]>([]);

  const loadMoreEvidence = async () => {
    if (!activeRunId || loadingMoreEvidence || evidenceList.length >= allEvidenceIds.length) return;
    setLoadingMoreEvidence(true);
    try {
      const nextBatchIds = allEvidenceIds.slice(evidenceList.length, evidenceList.length + 30);
      const evs = await Promise.all(
        nextBatchIds.map((id) => api.getEvidence(id, activeRunId).catch(() => null))
      );
      setEvidenceList((prev) => [...prev, ...evs.filter(Boolean)]);
    } catch (err) {
      console.error("Failed to load more evidence:", err);
    } finally {
      setLoadingMoreEvidence(false);
    }
  };

  const loadAllEvidence = async () => {
    if (!activeRunId || loadingMoreEvidence || evidenceList.length >= allEvidenceIds.length) return;
    setLoadingMoreEvidence(true);
    try {
      const remainingIds = allEvidenceIds.slice(evidenceList.length);
      const evs = await Promise.all(
        remainingIds.map((id) => api.getEvidence(id, activeRunId).catch(() => null))
      );
      setEvidenceList((prev) => [...prev, ...evs.filter(Boolean)]);
    } catch (err) {
      console.error("Failed to load all evidence:", err);
    } finally {
      setLoadingMoreEvidence(false);
    }
  };

  const toggleEvidenceExpanded = (id: string) => {
    setExpandedEvidenceIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllEvidence = () => {
    if (expandedEvidenceIds.size === evidenceList.length) {
      setExpandedEvidenceIds(new Set());
    } else {
      setExpandedEvidenceIds(new Set(evidenceList.map((e) => e.evidence_id)));
    }
  };

  const handleExport = async (format: "markdown" | "bibtex" | "json" | "zip") => {
    if (!activeRunId || exporting) return;
    setExporting(format);
    setExportError("");
    try {
      await api.exportRun(activeRunId, format);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting("");
    }
  };

  const fetchRunData = async (runId: string) => {
    try {
      const detail = await api.getRunDetail(runId);
      setActiveRunDetail(detail);

      // 1. Fetch delivery artifact content (check artifact_ids and artifact_refs)
      const artifactIds = detail.delivery?.artifact_ids || detail.delivery?.artifact_refs || [];
      if (artifactIds.length > 0) {
        try {
          const res = await api.getArtifactContent(runId, artifactIds[0]);
          let content = typeof res === "string" ? res : res.content;
          if (res.artifact?.media_type?.includes("json") && typeof content === "string") {
            try {
              const parsed = JSON.parse(content);
              content = parsed.answer || parsed.report || JSON.stringify(parsed, null, 2);
            } catch {}
          }
          setReportText(content || "");
        } catch (err) {
          console.error("Failed to load artifact content:", err);
          setReportText(detail.report_content || "");
        }
      } else {
        setReportText(detail.report_content || "");
      }

      // 2. Fetch evidence list
      const evidenceIds = detail.delivery?.evidence_ids || detail.delivery?.evidence_refs || [];
      setAllEvidenceIds(evidenceIds);
      if (evidenceIds.length > 0) {
        setTotalEvidenceCount(evidenceIds.length);
        try {
          const evs = await Promise.all(
            evidenceIds.slice(0, 30).map((id) => api.getEvidence(id, runId).catch(() => null))
          );
          setEvidenceList(evs.filter(Boolean));
        } catch (err) {
          console.error("Failed to load evidence items:", err);
        }
      } else if (detail.evidence) {
        setTotalEvidenceCount(detail.evidence.length);
        setEvidenceList(detail.evidence);
      } else {
        setTotalEvidenceCount(0);
        setEvidenceList([]);
      }

      // 3. Fetch execution trajectory steps
      try {
        const stepsRes = await api.getRunSteps(runId);
        if (stepsRes && Array.isArray(stepsRes.items)) {
          setRunSteps(stepsRes.items);
        } else if (Array.isArray(stepsRes)) {
          setRunSteps(stepsRes);
        }
      } catch (err) {
        console.warn("Failed to load run steps:", err);
      }
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    if (!activeRunId) return;
    setLoading(true);
    setReportText("");
    setEvidenceList([]);
    setAllEvidenceIds([]);
    setTotalEvidenceCount(0);
    setRunSteps([]);

    fetchRunData(activeRunId).finally(() => setLoading(false));

    // Live event subscription via SSE
    const unsubscribe = api.subscribeRunEvents(activeRunId, {
      onEvent: () => {
        api.getRunDetail(activeRunId).then((d) => setActiveRunDetail(d)).catch(() => {});
      },
      onTerminal: () => {
        fetchRunData(activeRunId);
      },
    });

    return () => {
      unsubscribe();
    };
  }, [activeRunId]);

  // Parse markdown headings for TOC
  useEffect(() => {
    if (!reportText) {
      setHeadings([]);
      return;
    }
    const lines = reportText.split("\n");
    const extracted: Array<{ id: string; text: string; level: number }> = [];
    let headingCount = 0;
    lines.forEach((line) => {
      const match = line.match(/^(#{1,3})\s+(.*)$/);
      if (match) {
        const level = match[1].length;
        const text = match[2].trim();
        const id = `heading-${headingCount++}`;
        extracted.push({ id, text, level });
      }
    });
    setHeadings(extracted);
  }, [reportText]);

  // Compile Academic Markdown with Anchored Headings, KaTeX Math, and Interactive Citation Pill Badges
  const renderedReportHtml = useMemo(() => {
    if (!reportText) return "";
    let headingCount = 0;
    const m = new Marked();
    m.use({
      renderer: {
        heading(token: any) {
          const id = `heading-${headingCount++}`;
          const content = this.parser.parseInline(token.tokens);
          return `<h${token.depth} id="${id}">${content}</h${token.depth}>`;
        },
      },
    });

    const mathTokens: { id: string; html: string }[] = [];
    let tokenCounter = 0;

    // Preserve display math $$ ... $$
    let md = reportText.replace(/\$\$([\s\S]*?)\$\$/g, (_, eq) => {
      const id = `@@KATEXBLOCK${tokenCounter++}@@`;
      try {
        const rendered = katex.renderToString(eq.trim(), {
          displayMode: true,
          throwOnError: false,
        });
        mathTokens.push({
          id,
          html: `<div class="katex-display my-3 overflow-x-auto text-center">${rendered}</div>`,
        });
      } catch {
        mathTokens.push({
          id,
          html: `<pre class="text-xs bg-muted p-2 rounded">$$${eq}$$</pre>`,
        });
      }
      return id;
    });

    // Preserve display math \[ ... \]
    md = md.replace(/\\\[([\s\S]*?)\\\]/g, (_, eq) => {
      const id = `@@KATEXBLOCK${tokenCounter++}@@`;
      try {
        const rendered = katex.renderToString(eq.trim(), {
          displayMode: true,
          throwOnError: false,
        });
        mathTokens.push({
          id,
          html: `<div class="katex-display my-3 overflow-x-auto text-center">${rendered}</div>`,
        });
      } catch {
        mathTokens.push({
          id,
          html: `<pre class="text-xs bg-muted p-2 rounded">\\[${eq}\\]</pre>`,
        });
      }
      return id;
    });

    // Preserve inline math \( ... \)
    md = md.replace(/\\\(([\s\S]*?)\\\)/g, (_, eq) => {
      const id = `@@KATEXINLINE${tokenCounter++}@@`;
      try {
        const rendered = katex.renderToString(eq.trim(), {
          displayMode: false,
          throwOnError: false,
        });
        mathTokens.push({ id, html: rendered });
      } catch {
        mathTokens.push({ id, html: `\\(${eq}\\)` });
      }
      return id;
    });

    // Preserve inline math $ ... $
    md = md.replace(/(?<!\\)\$([^\$\n]+?)(?<!\\)\$/g, (fullMatch, eq) => {
      if (/^\s*\d+([.,]\d+)?\s*$/.test(eq)) return fullMatch;
      const id = `@@KATEXINLINE${tokenCounter++}@@`;
      try {
        const rendered = katex.renderToString(eq.trim(), {
          displayMode: false,
          throwOnError: false,
        });
        mathTokens.push({ id, html: rendered });
      } catch {
        mathTokens.push({ id, html: `$${eq}$` });
      }
      return id;
    });

    // 1. Mark reference list anchors (e.g. - [1] or - [sq1-claim-0001] or 1. [sq1-claim-0001])
    md = md.replace(
      /^(\s*(?:-\s*|\d+\.\s*)\[)((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+|\d+|[a-zA-Z0-9_\-\.]+)(\]\s*)/gim,
      "$1@@REF_TARGET_$2@@$3"
    );

    // 2. Also mark inline claim tags in summary: e.g. 声明标识：`sq1-claim-0001`
    md = md.replace(
      /(声明标识：`?)((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+)(`?)/g,
      `$1<span id="cite-$2" class="citation-source-target font-mono font-bold text-primary underline">$2</span>$3`
    );

    // 3. Transform inline bracket citations (e.g. [1] or [sq1-claim-0001] or `[sq1-claim-0001]` or `[论点 sq1-claim-0001]`) into interactive badges
    md = md.replace(
      /`?\[(?:论点\s*|依据\s*|Claim\s*)?((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+|\d+)\]`?/gi,
      (match, id) => {
        let displayLabel = id;
        const sqMatch = id.match(/^sq(\d+)-claim-0*(\d+)$/i);
        if (sqMatch) {
          displayLabel = `Claim ${sqMatch[1]}.${sqMatch[2]}`;
        } else {
          const claimMatch = id.match(/^claim-0*(\d+)$/i);
          if (claimMatch) {
            displayLabel = `Claim ${claimMatch[1]}`;
          }
        }
        return `<a class="citation-badge" href="#cite-${id}" data-cite="${id}">[${displayLabel}]</a>`;
      }
    );

    // 4. Restore reference targets with anchor ID
    md = md.replace(
      /@@REF_TARGET_((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+|\d+|[a-zA-Z0-9_\-\.]+)@@/g,
      `<span id="cite-$1" class="citation-source-target font-mono font-bold text-primary inline-block mr-1.5">[<span class="underline">$1</span>]</span>`
    );

    let html = m.parse(md, { gfm: true, breaks: true }) as string;

    // Defense: in case any citation badge got escaped or wrapped inside <code>...</code>
    html = html.replace(/<code>\s*&lt;a class="citation-badge" href="#cite-([^"]+)" data-cite="([^"]+)"&gt;(\[.*?\])&lt;\/a&gt;\s*<\/code>/gi,
      '<a class="citation-badge" href="#cite-$1" data-cite="$2">$3</a>'
    );
    html = html.replace(/&lt;a class="citation-badge" href="#cite-([^"]+)" data-cite="([^"]+)"&gt;(\[.*?\])&lt;\/a&gt;/gi,
      '<a class="citation-badge" href="#cite-$1" data-cite="$2">$3</a>'
    );

    for (const token of mathTokens) {
      html = html.split(token.id).join(token.html);
    }
    return html;
  }, [reportText]);

  const handleReportClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = (e.target as HTMLElement).closest(".citation-badge");
    if (target) {
      const citeNum = target.getAttribute("data-cite");
      if (citeNum) {
        const refEl = document.getElementById(`cite-${citeNum}`);
        if (refEl) {
          refEl.scrollIntoView({ behavior: "smooth", block: "center" });
          refEl.classList.add("highlight-flash");
          setTimeout(() => refEl.classList.remove("highlight-flash"), 2200);
        }
      }
    }
  };

  const [actionLoading, setActionLoading] = useState(false);

  const handleCancel = async () => {
    if (!activeRunId || actionLoading) return;
    if (!window.confirm("确定要取消此研究任务吗？已生成的部分证据将保留。")) return;
    setActionLoading(true);
    try {
      await api.cancelRun(activeRunId);
      await fetchRunData(activeRunId);
    } catch (e: any) {
      alert(`取消失败: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleRerun = async () => {
    if (!activeRunId || actionLoading) return;
    setActionLoading(true);
    try {
      await api.rerunRun(activeRunId);
      await fetchRunData(activeRunId);
    } catch (e: any) {
      alert(`重试失败: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleRetry = async () => {
    if (!activeRunId || actionLoading) return;
    setActionLoading(true);
    try {
      await api.retryRun(activeRunId);
      await fetchRunData(activeRunId);
    } catch (e: any) {
      alert(`重试失败: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleFail = async () => {
    if (!activeRunId || actionLoading) return;
    if (!window.confirm("确定要终止并标记为失败吗？这将结束重试流程。")) return;
    setActionLoading(true);
    try {
      await api.failRun(activeRunId);
      await fetchRunData(activeRunId);
    } catch (e: any) {
      alert(`终止失败: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  if (!activeRunId) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-muted-foreground text-sm">
        请从左侧列表选择一项研究，或点击“新建研究”启动新的学术探索。
      </div>
    );
  }

  const delivery = activeRunDetail?.delivery;
  const limitations = delivery?.limitations || [];
  const recoveryActions = delivery?.recovery_actions || [];
  const steps = runSteps.length > 0 ? runSteps : (activeRunDetail?.steps || []);
  const currentStatus = activeRunDetail?.state || activeRunDetail?.status || (loading ? "loading" : "unknown");
  const runQuery = activeRunDetail?.query || activeRunDetail?.task_input?.query || activeRunDetail?.task_input?.objective || activeRunDetail?.research_context?.query || activeRunId;

  const reportCharCount = reportText.length;
  const reportWordCount = (reportText.match(/[\u4e00-\u9fff]|[a-zA-Z0-9_-]+/g) || []).length;

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* Studio Header Bar */}
      <div className="shrink-0 flex items-center justify-between px-6 py-2.5 border-b border-border/70 bg-card/60 backdrop-blur-sm">
        <div className="flex items-center space-x-3 min-w-0">
          <span
            className={cn(
              "px-2.5 py-0.5 rounded-full text-[11px] font-mono font-medium tracking-wide uppercase",
              currentStatus === "complete"
                ? "bg-emerald-800 text-emerald-100 dark:bg-emerald-900 dark:text-emerald-200"
                : currentStatus === "partial"
                ? "bg-amber-800 text-amber-100"
                : currentStatus === "failed"
                ? "bg-rose-800 text-rose-100"
                : "bg-muted text-muted-foreground"
            )}
          >
            {currentStatus}
          </span>
          <h2 className="text-sm font-serif-academic font-semibold text-foreground truncate max-w-xl">
            {runQuery}
          </h2>
        </div>

        <div className="flex items-center space-x-2">
          {(currentStatus === "working" || currentStatus === "pending" || currentStatus === "running") && (
            <Button
              variant="outline"
              size="sm"
              disabled={actionLoading}
              onClick={handleCancel}
              className="gap-1.5 text-xs text-destructive border-destructive/40 hover:bg-destructive/10 font-serif-academic cursor-pointer"
            >
              <span>取消任务</span>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIsFollowUpOpen(true)}
            className="gap-1.5 text-xs border-border/80 hover:border-foreground/30 font-serif-academic"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" />
            <span>跟进追问</span>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={actionLoading}
            onClick={handleRerun}
            title="重新运行当前任务"
            className="gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>重新研究</span>
          </Button>
        </div>
      </div>

      {/* Main Studio Body: Adaptive Studio Tri-Pane */}
      <div className="flex-1 flex overflow-hidden">
        {/* Middle: Golden Reading Canvas (840px max width) */}
        <div className="flex-1 overflow-y-auto px-6 py-8 flex justify-center bg-background">
          <article className="w-full max-w-[840px] space-y-6">
            {currentStatus === "needs_attention" && (
              <div className="p-4 rounded-xl border border-amber-500/40 bg-amber-50/70 dark:bg-amber-950/30 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs sm:text-sm shadow-xs">
                <div className="flex items-center gap-2 text-amber-800 dark:text-amber-300">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>任务遇到不确定的外部调用（如付费调用后网络超时），请选择人工恢复策略：</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button
                    size="sm"
                    variant="default"
                    disabled={actionLoading}
                    onClick={handleRetry}
                    className="bg-amber-600 hover:bg-amber-700 text-white text-xs h-7 cursor-pointer"
                  >
                    重试外部调用
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={actionLoading}
                    onClick={handleFail}
                    className="border-amber-600/40 text-amber-800 dark:text-amber-300 hover:bg-amber-100/50 text-xs h-7 cursor-pointer"
                  >
                    终止并标记失败
                  </Button>
                </div>
              </div>
            )}
            {loading ? (
              <div className="space-y-4 py-8 animate-pulse">
                <div className="h-8 bg-muted rounded w-2/3" />
                <div className="h-4 bg-muted rounded w-full" />
                <div className="h-4 bg-muted rounded w-4/5" />
                <div className="h-4 bg-muted rounded w-full" />
              </div>
            ) : reportText ? (
              <div className="space-y-6">
                <div className="p-6 rounded-2xl border border-border/80 bg-card shadow-xs top-bevel">
                  <h1 className="text-xl sm:text-2xl font-serif-academic font-bold tracking-tight text-foreground mb-3 leading-snug">
                    {runQuery}
                  </h1>
                  <div className="flex flex-wrap items-center gap-3 text-xs sm:text-sm text-foreground/80 font-mono">
                    <span className="font-semibold">Run ID: {activeRunId}</span>
                    <span>·</span>
                    <span>状态: {delivery?.disposition || currentStatus || "N/A"}</span>
                    {reportText && (
                      <>
                        <span>·</span>
                        <span className="text-foreground/90 font-medium">
                          字数: {reportCharCount.toLocaleString()} 字 ({reportWordCount.toLocaleString()} 词)
                        </span>
                      </>
                    )}
                    {totalEvidenceCount > 0 && (
                      <>
                        <span>·</span>
                        <button
                          type="button"
                          onClick={() => setRightTab("evidence")}
                          className="text-emerald-800 dark:text-emerald-300 font-semibold flex items-center gap-1 cursor-pointer hover:underline text-left bg-transparent border-none p-0"
                          title="查看右侧全部核验证据明细"
                        >
                          <ShieldCheck className="h-4 w-4" />
                          <span>
                            {evidenceList.length < totalEvidenceCount
                              ? `${evidenceList.length} / 共 ${totalEvidenceCount} 处核验证据已锚定 (点击查看)`
                              : `${totalEvidenceCount} 处核验证据已锚定 (点击查看)`}
                          </span>
                        </button>
                      </>
                    )}
                  </div>
                  {activeRunDetail?.budget && (
                    <div className="mt-2 text-xs font-mono text-foreground/70 flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-semibold text-foreground/80">预算</span>
                      <span>token {activeRunDetail.budget.input_tokens_used ?? 0}/{activeRunDetail.budget.input_tokens_limit ?? 0}</span>
                      <span>工具 {activeRunDetail.budget.tool_calls_used ?? 0}/{activeRunDetail.budget.tool_calls_limit ?? 0}</span>
                      <span>预留 {activeRunDetail.budget.tool_calls_reserved ?? 0}</span>
                      <span>剩余 {activeRunDetail.budget.tool_calls_remaining ?? 0}</span>
                      <span>时钟 {activeRunDetail.budget.wall_clock_seconds_remaining ?? 0}s</span>
                      {activeRunDetail.budget.state === "stopped" && (
                        <span className="text-rose-600 dark:text-rose-400 font-semibold">已超限停止</span>
                      )}
                    </div>
                  )}
                  <div className="mt-3 pt-3 border-t border-border/60 flex flex-wrap items-center gap-2">
                    <span className="text-xs text-foreground/70 flex items-center gap-1 font-mono">
                      <Download className="h-3.5 w-3.5" />
                      导出
                    </span>
                    {(["markdown", "bibtex", "json", "zip"] as const).map((format) => (
                      <Button
                        key={format}
                        variant="outline"
                        size="sm"
                        className="h-7 px-2.5 text-xs font-mono"
                        disabled={!!exporting}
                        onClick={() => handleExport(format)}
                        title={
                          format === "markdown"
                            ? "导出 Markdown（含元数据与引用列表）"
                            : format === "bibtex"
                              ? "导出 BibTeX 文献条目"
                              : format === "json"
                                ? "导出 JSON（含 Evidence 台账）"
                                : "导出 ZIP 证据包（report.md + references.bib + evidence.json）"
                        }
                      >
                        {exporting === format ? "导出中…" : format.toUpperCase()}
                      </Button>
                    ))}
                    {exportError && (
                      <span className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        导出失败：{exportError}
                      </span>
                    )}
                  </div>
                </div>

                {/* Academic Report Reading Canvas */}
                <div
                  className="p-8 sm:p-12 rounded-2xl border border-border/80 bg-card text-foreground shadow-xs top-bevel academic-report max-w-none"
                  onClick={handleReportClick}
                  dangerouslySetInnerHTML={{
                    __html: renderedReportHtml,
                  }}
                />
              </div>
            ) : (
              <div className="text-center py-20 text-foreground/75 text-sm sm:text-base font-serif-academic">
                暂无报告正文交付物。任务可能处于进行中或离线桩模式。
              </div>
            )}
          </article>
        </div>

        {/* Right: Fused TOC & Evidence Context Drawer */}
        <aside className="w-80 shrink-0 border-l border-border/70 bg-card/30 flex flex-col h-full">
          <Tabs
            value={rightTab}
            onValueChange={(v) => setRightTab(v as any)}
            className="flex-1 flex flex-col h-full"
          >
            <div className="p-2 border-b border-border/60">
              <TabsList className="w-full grid grid-cols-3 h-8">
                <TabsTrigger value="toc" className="text-xs">
                  <ListTree className="h-3.5 w-3.5 mr-1" />
                  <span>目录</span>
                </TabsTrigger>
                <TabsTrigger value="evidence" className="text-xs">
                  <ShieldCheck className="h-3.5 w-3.5 mr-1" />
                  <span>
                    证据 ({evidenceList.length}
                    {totalEvidenceCount > evidenceList.length ? ` / ${totalEvidenceCount}` : ""})
                  </span>
                </TabsTrigger>
                <TabsTrigger value="activity" className="text-xs">
                  <Activity className="h-3.5 w-3.5 mr-1" />
                  <span>轨迹</span>
                </TabsTrigger>
              </TabsList>
            </div>

            {/* Tab: Table of Contents */}
            <TabsContent value="toc" className="flex-1 overflow-y-auto p-3 space-y-2 mt-0">
              <span className="text-xs font-mono uppercase tracking-wider text-foreground/80 font-bold block mb-2">
                Report Navigation / 报告导航
              </span>
              {headings.length === 0 ? (
                <div className="text-xs sm:text-sm text-foreground/75 py-6 text-center">未检测到章节目录</div>
              ) : (
                <nav className="space-y-1 text-xs">
                  {headings.map((h) => {
                    const badgeColor =
                      h.level === 1
                        ? "text-emerald-800 dark:text-emerald-300 bg-emerald-100/80 dark:bg-emerald-950/60 border-emerald-300 dark:border-emerald-800"
                        : h.level === 2
                        ? "text-purple-800 dark:text-purple-300 bg-purple-100/80 dark:bg-purple-950/60 border-purple-300 dark:border-purple-800"
                        : h.level === 3
                        ? "text-sky-800 dark:text-sky-300 bg-sky-100/80 dark:bg-sky-950/60 border-sky-300 dark:border-sky-800"
                        : h.level === 4
                        ? "text-teal-800 dark:text-teal-300 bg-teal-100/80 dark:bg-teal-950/60 border-teal-300 dark:border-teal-800"
                        : h.level === 5
                        ? "text-green-800 dark:text-green-300 bg-green-100/80 dark:bg-green-950/60 border-green-300 dark:border-green-800"
                        : "text-rose-800 dark:text-rose-300 bg-rose-100/80 dark:bg-rose-950/60 border-rose-300 dark:border-rose-800";

                    return (
                      <button
                        key={h.id}
                        onClick={() => {
                          const el = document.getElementById(h.id);
                          if (el) {
                            el.scrollIntoView({ behavior: "smooth", block: "start" });
                          }
                        }}
                        className={cn(
                          "w-full text-left py-1.5 px-2 rounded-md transition text-foreground/85 hover:text-foreground hover:bg-muted/70 flex items-center justify-between group cursor-pointer",
                          h.level === 1
                            ? "font-semibold text-foreground text-xs"
                            : h.level === 2
                            ? "pl-4 text-xs font-normal text-foreground/80"
                            : "pl-6 text-[11px] text-foreground/70"
                        )}
                      >
                        <span className="truncate group-hover:underline">{h.text}</span>
                        <span
                          className={cn(
                            "text-[9px] font-mono font-semibold px-1 py-0.5 rounded border shrink-0 ml-1.5 transition-colors",
                            badgeColor
                          )}
                        >
                          H{h.level}
                        </span>
                      </button>
                    );
                  })}
                </nav>
              )}
            </TabsContent>

            {/* Tab: Evidence & Recoveries */}
            <TabsContent value="evidence" className="flex-1 overflow-y-auto p-3 space-y-3 mt-0">
              {limitations.length > 0 && (
                <div className="p-3 rounded-lg border border-amber-500/30 bg-amber-500/10 text-xs sm:text-sm space-y-1">
                  <div className="flex items-center space-x-1.5 font-semibold text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>已知局限性 (Limitations)</span>
                  </div>
                  <ul className="list-disc list-inside space-y-1 text-foreground/80 pl-1 text-xs sm:text-sm">
                    {limitations.map((lim, i) => (
                      <li key={i}>{lim}</li>
                    ))}
                  </ul>
                </div>
              )}

              {recoveryActions.length > 0 && (
                <div className="p-3 rounded-lg border border-border/80 bg-muted/30 text-xs sm:text-sm space-y-1">
                  <span className="font-semibold text-foreground">建议恢复动作:</span>
                  <ul className="space-y-1 text-foreground/80">
                    {recoveryActions.map((rec, i) => (
                      <li key={i} className="flex items-start space-x-1.5">
                        <ChevronRight className="h-3.5 w-3.5 mt-0.5 shrink-0 text-emerald-600" />
                        <span>{rec}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="flex items-center justify-between pt-2">
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/80 font-bold block">
                  Grounding Facts / 事实证据卡片 ({evidenceList.length}
                  {totalEvidenceCount > evidenceList.length ? ` / 共 ${totalEvidenceCount} 条` : ""})
                </span>
                {evidenceList.length > 0 && (
                  <button
                    onClick={toggleAllEvidence}
                    className="text-xs text-emerald-800 dark:text-emerald-300 hover:underline font-serif-academic cursor-pointer font-medium"
                  >
                    {expandedEvidenceIds.size === evidenceList.length ? "全部折叠" : "全部展开"}
                  </button>
                )}
              </div>
              {evidenceList.length === 0 ? (
                <div className="text-xs sm:text-sm text-foreground/75 py-6 text-center">暂无结构化证据记录</div>
              ) : (
                evidenceList.map((ev) => {
                  const isExpanded = expandedEvidenceIds.has(ev.evidence_id);
                  return (
                    <div
                      key={ev.evidence_id}
                      className="rounded-lg border border-border/80 bg-card text-xs sm:text-sm shadow-2xs top-bevel hover:border-emerald-800/40 transition overflow-hidden"
                    >
                      <div
                        onClick={() => toggleEvidenceExpanded(ev.evidence_id)}
                        className="p-3 flex items-center justify-between cursor-pointer hover:bg-muted/30 transition text-xs font-mono select-none"
                      >
                        <div className="flex items-center gap-1.5 min-w-0">
                          {isExpanded ? (
                            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          )}
                          <span className="font-bold text-foreground truncate">{ev.evidence_id}</span>
                          {(ev.modality === "image" || ev.asset_id) && (
                            <Badge variant="outline" className="text-[10px] px-1 py-0 text-emerald-700 dark:text-emerald-300 border-emerald-600/30">
                              图表
                            </Badge>
                          )}
                        </div>
                        <span className="text-emerald-800 dark:text-emerald-300 font-semibold shrink-0 ml-2">
                          {ev.locator?.page ? `第 ${ev.locator.page} 页` : ev.locator?.heading || ev.extraction_method || "核验证据"}
                        </span>
                      </div>

                      {isExpanded && (
                        <div className="p-3 pt-0 border-t border-border/50 space-y-2 mt-1">
                          {(ev.modality === "image" || ev.asset_id) ? (
                            <div className="space-y-2 pt-1">
                              <div
                                className="relative rounded-md overflow-hidden border border-border/70 bg-muted/20 cursor-pointer group flex items-center justify-center p-1 hover:border-emerald-700/60 transition"
                                onClick={() =>
                                  setPreviewImage({
                                    url: `/api/v1/library/assets/${ev.asset_id}/content`,
                                    title: `视觉实证图片：${ev.evidence_id} (第 ${ev.locator?.page || 1} 页)`,
                                  })
                                }
                              >
                                <img
                                  src={`/api/v1/library/assets/${ev.asset_id}/content?variant=thumbnail`}
                                  alt={ev.quote || "视觉实证图表"}
                                  className="w-full max-h-48 object-contain bg-background/50 rounded transition-transform group-hover:scale-[1.01]"
                                  onError={(e) => {
                                    (e.target as HTMLElement).setAttribute("src", `/api/v1/library/assets/${ev.asset_id}/content`);
                                  }}
                                />
                                <div className="absolute bottom-1.5 right-1.5 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded font-mono flex items-center space-x-1 opacity-80 group-hover:opacity-100 transition">
                                  <ImageIcon className="h-3 w-3" />
                                  <span>点击放大原图</span>
                                </div>
                              </div>
                              {ev.quote && (
                                <p className="text-foreground/90 leading-relaxed italic border-l-2 border-emerald-800/60 pl-2.5 my-1 font-serif-academic text-xs">
                                  “{ev.quote}”
                                </p>
                              )}
                            </div>
                          ) : (
                            <p className="text-foreground/90 leading-relaxed italic border-l-2 border-emerald-800/60 pl-2.5 my-1 font-serif-academic">
                              “{ev.quote}”
                            </p>
                          )}
                          {ev.source_snapshot_id && (
                            <div className="text-xs font-mono text-foreground/70 truncate pt-0.5">
                              来源: {ev.source_snapshot_id.length > 32 ? `${ev.source_snapshot_id.slice(0, 24)}...` : ev.source_snapshot_id}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}

              {totalEvidenceCount > evidenceList.length && (
                <div className="p-3 bg-muted/40 border border-border/70 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground font-mono">
                    已载入 {evidenceList.length} 条，尚有 {totalEvidenceCount - evidenceList.length} 条未加载
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={loadingMoreEvidence}
                      onClick={loadMoreEvidence}
                      className="h-7 text-xs"
                    >
                      {loadingMoreEvidence ? "加载中..." : "加载下 30 条"}
                    </Button>
                    <Button
                      size="sm"
                      disabled={loadingMoreEvidence}
                      onClick={loadAllEvidence}
                      className="h-7 text-xs bg-emerald-800 hover:bg-emerald-900 text-white"
                    >
                      {loadingMoreEvidence ? "加载中..." : "加载全部"}
                    </Button>
                  </div>
                </div>
              )}
            </TabsContent>

            {/* Tab: Activity Logs / Execution Trajectory */}
            <TabsContent value="activity" className="flex-1 overflow-y-auto p-3 space-y-3 mt-0">
              <div className="flex items-center justify-between pb-1 border-b border-border/60">
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/80 font-bold block">
                  Execution Steps / 执行轨迹
                </span>
                {activeRunDetail?.progress && (
                  <span className="text-[11px] font-mono text-muted-foreground">
                    进度: {activeRunDetail.progress.completed_steps} / {activeRunDetail.progress.total_steps}
                  </span>
                )}
              </div>

              {activeRunDetail?.progress?.current_phase && (
                <div className="p-2 rounded bg-emerald-500/10 border border-emerald-500/20 text-xs font-serif-academic text-emerald-800 dark:text-emerald-300">
                  <span className="font-semibold">当前阶段：</span>
                  {activeRunDetail.progress.current_phase}
                </div>
              )}

              {steps.length === 0 ? (
                <div className="text-center py-12 text-xs text-muted-foreground font-serif-academic space-y-1">
                  <Activity className="h-6 w-6 mx-auto text-muted-foreground/50 mb-2" />
                  <p>暂无底层执行步骤明细</p>
                  <p className="text-[11px] text-muted-foreground/70">
                    {activeRunDetail?.progress?.last_event_message || "任务状态已由调度平面接管"}
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {steps.map((st, idx) => {
                    const kindName =
                      st.kind === "execute_research"
                        ? "多源文献深度检索与证据交叉论证"
                        : st.kind === "publish_delivery"
                        ? "成果合成与闭环报告发布"
                        : st.kind === "document_reading"
                        ? "文献研读精读归档"
                        : st.kind === "subquestion_plan"
                        ? "研究目标分解与子课题规划"
                        : st.kind;
                    const isSuccess = st.status === "completed" || (st.status as string) === "succeeded";
                    const isRunning = st.status === "running";
                    const isFailed = st.status === "failed";

                    return (
                      <div
                        key={st.step_id || idx}
                        className="p-3 rounded-lg border border-border/70 bg-card text-xs space-y-1.5 shadow-2xs top-bevel"
                      >
                        <div className="flex justify-between items-start gap-2">
                          <div className="space-y-0.5">
                            <span className="font-serif-academic text-foreground font-semibold block text-xs sm:text-sm">
                              {kindName}
                            </span>
                            <span className="font-mono text-[10px] text-muted-foreground block">
                              ID: {st.step_id}
                            </span>
                          </div>
                          <Badge
                            variant={isSuccess ? "default" : isFailed ? "destructive" : "secondary"}
                            className={`text-[10px] font-mono shrink-0 ${
                              isSuccess
                                ? "bg-emerald-700 text-white"
                                : isRunning
                                ? "bg-blue-600 text-white animate-pulse"
                                : ""
                            }`}
                          >
                            {isSuccess
                              ? "✓ 成功完成"
                              : isRunning
                              ? "执行中..."
                              : isFailed
                              ? "✕ 执行失败"
                              : st.status}
                          </Badge>
                        </div>
                        <div className="flex items-center justify-between text-[11px] text-muted-foreground font-mono pt-1 border-t border-border/40">
                          <span>Attempt #{st.attempt}</span>
                          <span className="text-[10px] uppercase font-mono tracking-wider">
                            {st.kind}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </aside>
      </div>

      {previewImage && (
        <Dialog open={!!previewImage} onOpenChange={(open) => !open && setPreviewImage(null)}>
          <DialogContent className="max-w-4xl max-h-[90vh] p-4 bg-background flex flex-col">
            <DialogHeader>
              <DialogTitle className="text-sm font-serif-academic font-bold truncate text-foreground">
                {previewImage.title}
              </DialogTitle>
            </DialogHeader>
            <div className="flex-1 overflow-auto flex items-center justify-center p-3 bg-muted/20 rounded-lg">
              <img
                src={previewImage.url}
                alt="Evidence Asset"
                className="max-h-[72vh] max-w-full object-contain rounded shadow-xs"
              />
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
};
