import React, { useEffect, useState, useMemo } from "react";
import { Marked } from "marked";
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
  const [previewImage, setPreviewImage] = useState<{ url: string; title: string } | null>(null);

  useEffect(() => {
    if (!activeRunId) return;
    setLoading(true);
    setReportText("");
    setEvidenceList([]);

    api.getRunDetail(activeRunId)
      .then(async (detail) => {
        setActiveRunDetail(detail);

        // 1. Fetch delivery artifact content (check artifact_ids and artifact_refs)
        const artifactIds = detail.delivery?.artifact_ids || detail.delivery?.artifact_refs || [];
        if (artifactIds.length > 0) {
          try {
            const res = await api.getArtifactContent(activeRunId, artifactIds[0]);
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
        if (evidenceIds.length > 0) {
          try {
            const evs = await Promise.all(
              evidenceIds.slice(0, 30).map((id) => api.getEvidence(id, activeRunId).catch(() => null))
            );
            setEvidenceList(evs.filter(Boolean));
          } catch (err) {
            console.error("Failed to load evidence items:", err);
          }
        } else if (detail.evidence) {
          setEvidenceList(detail.evidence);
        }
      })
      .catch((err) => {
        console.error(err);
      })
      .finally(() => setLoading(false));
  }, [activeRunId, setActiveRunDetail]);

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

  // Compile Academic Markdown with Anchored Headings and Interactive Citation Pill Badges
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

    // 1. Mark reference list anchors (e.g. - [1]《Title》)
    let md = reportText.replace(/^-\s*\[(\d+)\]\s*(.*)$/gm, "- @@REF_TARGET_$1@@ $2");
    // 2. Transform inline bracket citations (e.g. [1][2]) into interactive badges
    md = md.replace(/\[(\d+)\]/g, `<a class="citation-badge" href="#cite-$1" data-cite="$1">[$1]</a>`);
    // 3. Restore reference targets with anchor ID
    md = md.replace(
      /@@REF_TARGET_(\d+)@@/g,
      `<span id="cite-$1" class="citation-source-target font-mono font-bold text-primary inline-block mr-1.5">[<span class="underline">$1</span>]</span>`
    );

    return m.parse(md, { gfm: true, breaks: true }) as string;
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

  const handleRerun = async () => {
    if (!activeRunId) return;
    try {
      await api.rerunRun(activeRunId);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleRetry = async () => {
    if (!activeRunId) return;
    try {
      await api.retryRun(activeRunId);
    } catch (e: any) {
      alert(e.message);
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
  const steps = activeRunDetail?.steps || [];
  const currentStatus = activeRunDetail?.state || activeRunDetail?.status || (loading ? "loading" : "unknown");
  const runQuery = activeRunDetail?.query || activeRunDetail?.task_input?.query || activeRunDetail?.task_input?.objective || activeRunDetail?.research_context?.query || activeRunId;

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
                    {evidenceList.length > 0 && (
                      <>
                        <span>·</span>
                        <button
                          type="button"
                          onClick={() => setRightTab("evidence")}
                          className="text-emerald-800 dark:text-emerald-300 font-semibold flex items-center gap-1 cursor-pointer hover:underline text-left bg-transparent border-none p-0"
                          title="查看右侧全部核验证据明细"
                        >
                          <ShieldCheck className="h-4 w-4" />
                          <span>{evidenceList.length} 处核验证据已锚定 (点击查看)</span>
                        </button>
                      </>
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
                  <span>证据 ({evidenceList.length})</span>
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

              <span className="text-xs font-mono uppercase tracking-wider text-foreground/80 font-bold block pt-2">
                Grounding Facts / 事实证据卡片
              </span>
              {evidenceList.length === 0 ? (
                <div className="text-xs sm:text-sm text-foreground/75 py-6 text-center">暂无结构化证据记录</div>
              ) : (
                evidenceList.map((ev) => (
                  <div
                    key={ev.evidence_id}
                    className="p-3.5 rounded-lg border border-border/80 bg-card text-xs sm:text-sm space-y-2 shadow-2xs top-bevel hover:border-emerald-800/40 transition"
                  >
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="font-bold text-foreground">{ev.evidence_id}</span>
                      <span className="text-emerald-800 dark:text-emerald-300 font-semibold">
                        {ev.locator?.page ? `第 ${ev.locator.page} 页` : ev.locator?.heading || ev.extraction_method || "核验证据"}
                      </span>
                    </div>
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
                ))
              )}
            </TabsContent>

            {/* Tab: Activity Logs */}
            <TabsContent value="activity" className="flex-1 overflow-y-auto p-3 space-y-2 mt-0">
              <span className="text-xs font-mono uppercase tracking-wider text-foreground/80 font-bold block">
                Execution Steps / 执行轨迹
              </span>
              {steps.map((st) => (
                <div
                  key={st.step_id}
                  className="p-2.5 rounded-md border border-border/60 bg-muted/20 text-xs sm:text-sm space-y-1"
                >
                  <div className="flex justify-between items-center">
                    <span className="font-mono text-foreground font-semibold">{st.kind}</span>
                    <Badge variant={st.status === "completed" ? "moss" : "secondary"} className="text-xs font-mono">
                      {st.status}
                    </Badge>
                  </div>
                  <div className="text-xs text-foreground/70 font-mono">
                    Attempt #{st.attempt}
                  </div>
                </div>
              ))}
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
