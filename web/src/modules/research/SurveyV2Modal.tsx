import React, { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Loader2,
  BookOpen,
  FileText,
  ListTree,
  ExternalLink,
  Copy,
  Download,
  Check,
  Compass,
  Layers,
  Sparkles,
  GitBranch,
} from "lucide-react";
import { api } from "@/services/api";
import { renderMarkdownWithMath } from "@/lib/math";
import type {
  LongHorizonSurveyOutlineResponse,
  LongHorizonSurveyReportResponse,
} from "@/types/workbench";

interface SurveyV2ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialQuery?: string;
}

export const SurveyV2Modal: React.FC<SurveyV2ModalProps> = ({
  open,
  onOpenChange,
  initialQuery = "",
}) => {
  const [objective, setObjective] = useState(initialQuery);
  const [backgroundContext, setBackgroundContext] = useState("");
  const [targetWordCount, setTargetWordCount] = useState<number>(6000);
  const [allowNetwork, setAllowNetwork] = useState(true);

  const [loadingOutline, setLoadingOutline] = useState(false);
  const [loadingExecute, setLoadingExecute] = useState(false);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<"outline" | "report" | "timeline" | "ledger">("outline");

  const [outline, setOutline] = useState<LongHorizonSurveyOutlineResponse | null>(null);
  const [report, setReport] = useState<LongHorizonSurveyReportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Sync initial query if changed
  React.useEffect(() => {
    if (initialQuery && !objective) {
      setObjective(initialQuery);
    }
  }, [initialQuery]);

  const handlePlanOutline = async () => {
    if (!objective.trim()) return;
    setLoadingOutline(true);
    setError(null);
    try {
      const res = await api.planSurveyOutline({
        objective: objective.trim(),
        background_context: backgroundContext.trim() || undefined,
        max_sources: Math.min(100, Math.max(10, Math.round(targetWordCount / 100))),
        allow_network: allowNetwork,
      });
      setOutline(res);
      setActiveTab("outline");
    } catch (err: any) {
      setError(err.message || "规划大纲失败");
    } finally {
      setLoadingOutline(false);
    }
  };

  const handleExecuteSurvey = async () => {
    if (!objective.trim()) return;
    setLoadingExecute(true);
    setError(null);
    try {
      const res = await api.executeSurvey({
        objective: objective.trim(),
        background_context: backgroundContext.trim() || undefined,
        max_sources: Math.min(100, Math.max(10, Math.round(targetWordCount / 100))),
        allow_network: allowNetwork,
      });
      setReport(res);
      setActiveTab("report");
    } catch (err: any) {
      setError(err.message || "执行长程综述失败");
    } finally {
      setLoadingExecute(false);
    }
  };

  const handleCopyMarkdown = () => {
    if (!report?.full_markdown) return;
    navigator.clipboard.writeText(report.full_markdown);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadMarkdown = () => {
    if (!report?.full_markdown) return;
    const blob = new Blob([report.full_markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Survey_${report.objective.slice(0, 30).replace(/[^\w\u4e00-\u9fa5]/g, "_")}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[92vh] flex flex-col p-6 overflow-hidden">
        <DialogHeader className="shrink-0 border-b border-border/60 pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2.5">
              <Compass className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />
              <DialogTitle className="text-base font-serif-academic font-bold">
                长程学术综述与假设树规划 (Deep Research V2)
              </DialogTitle>
            </div>
            <Badge variant="outline" className="font-mono text-xs">
              {allowNetwork ? "🌐 真实网络检索 (OpenAlex/arXiv)" : "📁 本地确定性证据"}
            </Badge>
          </div>
          <DialogDescription className="text-xs text-muted-foreground font-serif-academic pt-1">
            基于多源真实信源检索、双层探针矩阵与演化时序拓扑，生成篇幅 5,000~12,000 字的高严谨性学术综述。
          </DialogDescription>
        </DialogHeader>

        {/* Input Configuration Bar */}
        <div className="shrink-0 space-y-3 py-3 border-b border-border/40 bg-muted/20 px-4 rounded-lg mt-2">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-2 space-y-1">
              <label className="text-xs font-semibold text-foreground">综述目标 / 核心课题 *</label>
              <Input
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                placeholder="例如：长序列大模型注意力优化与块稀疏架构演化脉络综述"
                className="text-xs font-serif-academic"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-semibold text-foreground">目标篇幅 (字数)</label>
              <div className="flex items-center space-x-2">
                <input
                  type="range"
                  min={4000}
                  max={12000}
                  step={1000}
                  value={targetWordCount}
                  onChange={(e) => setTargetWordCount(Number(e.target.value))}
                  className="w-full h-1.5 bg-muted rounded appearance-none cursor-pointer"
                />
                <span className="font-mono text-xs font-bold text-emerald-800 dark:text-emerald-300 shrink-0 w-16">
                  {targetWordCount} 字
                </span>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <div className="flex items-center space-x-3 text-xs">
              <label className="flex items-center space-x-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={allowNetwork}
                  onChange={(e) => setAllowNetwork(e.target.checked)}
                  className="rounded border-border text-emerald-600 focus:ring-emerald-500"
                />
                <span className="text-foreground/80">启用联网检索 (OpenAlex / arXiv API)</span>
              </label>
            </div>

            <div className="flex items-center space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handlePlanOutline}
                disabled={loadingOutline || loadingExecute || !objective.trim()}
                className="text-xs font-serif-academic gap-1.5"
              >
                {loadingOutline ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>规划中...</span>
                  </>
                ) : (
                  <>
                    <ListTree className="h-3.5 w-3.5" />
                    <span>推演大纲与探针</span>
                  </>
                )}
              </Button>

              <Button
                size="sm"
                onClick={handleExecuteSurvey}
                disabled={loadingOutline || loadingExecute || !objective.trim()}
                className="text-xs font-serif-academic gap-1.5 bg-emerald-800 hover:bg-emerald-900 text-white"
              >
                {loadingExecute ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>研读与合成中...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="h-3.5 w-3.5" />
                    <span>执行长程综述合成</span>
                  </>
                )}
              </Button>
            </div>
          </div>

          {error && (
            <div className="text-xs text-rose-600 bg-rose-500/10 p-2 rounded border border-rose-500/20">
              {error}
            </div>
          )}
        </div>

        {/* Content Navigation Tabs */}
        {(outline || report) && (
          <div className="shrink-0 flex items-center justify-between border-b border-border/60 pt-3 pb-2">
            <div className="flex items-center space-x-2 text-xs">
              {outline && (
                <button
                  type="button"
                  onClick={() => setActiveTab("outline")}
                  className={`px-3 py-1 rounded font-serif-academic font-medium transition ${
                    activeTab === "outline"
                      ? "bg-primary text-white"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  }`}
                >
                  大纲与探针矩阵 ({outline.chapters?.length || 0}章 / {outline.total_probes_count || 0}探针)
                </button>
              )}
              {report && (
                <>
                  <button
                    type="button"
                    onClick={() => setActiveTab("report")}
                    className={`px-3 py-1 rounded font-serif-academic font-medium transition ${
                      activeTab === "report"
                        ? "bg-primary text-white"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                    }`}
                  >
                    综述正文 ({report.total_word_count || 0} 字)
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveTab("timeline")}
                    className={`px-3 py-1 rounded font-serif-academic font-medium transition ${
                      activeTab === "timeline"
                        ? "bg-primary text-white"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                    }`}
                  >
                    演化时序与对比矩阵
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveTab("ledger")}
                    className={`px-3 py-1 rounded font-serif-academic font-medium transition ${
                      activeTab === "ledger"
                        ? "bg-primary text-white"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                    }`}
                  >
                    严谨引文台账 ({report.total_sources_cited || 0} 篇)
                  </button>
                </>
              )}
            </div>

            {report && (
              <div className="flex items-center space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleCopyMarkdown}
                  className="h-7 text-xs font-serif-academic gap-1"
                >
                  {copied ? <Check className="h-3 w-3 text-emerald-600" /> : <Copy className="h-3 w-3" />}
                  <span>{copied ? "已复制" : "复制 Markdown"}</span>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleDownloadMarkdown}
                  className="h-7 text-xs font-serif-academic gap-1"
                >
                  <Download className="h-3 w-3" />
                  <span>导出文件</span>
                </Button>
              </div>
            )}
          </div>
        )}

        {/* Scrollable Main Display Body */}
        <div className="flex-1 overflow-y-auto pt-3 space-y-4">
          {!outline && !report && !loadingOutline && !loadingExecute && (
            <div className="h-64 flex flex-col items-center justify-center text-center text-muted-foreground p-8">
              <Compass className="h-10 w-10 text-muted-foreground/40 mb-3" />
              <p className="text-sm font-serif-academic font-medium text-foreground/80">
                输入学术课题并点击【推演大纲】或【执行长程综述合成】
              </p>
              <p className="text-xs text-muted-foreground max-w-md mt-1">
                系统将通过开放学术接口自动检索相关前沿文献，规划递进论证章节与微观论据探针，生成全景式文献综述。
              </p>
            </div>
          )}

          {(loadingOutline || loadingExecute) && (
            <div className="h-64 flex flex-col items-center justify-center text-center p-8">
              <Loader2 className="h-8 w-8 animate-spin text-emerald-700 dark:text-emerald-400 mb-3" />
              <p className="text-sm font-serif-academic font-semibold text-foreground">
                {loadingOutline ? "正在调用学术引擎推演大纲与探针..." : "正在并行检索权威文献并合成章节..."}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {allowNetwork ? "检索通道: OpenAlex & arXiv 实时文献检索" : "检索通道: 本地确定性学术证据库"}
              </p>
            </div>
          )}

          {/* TAB 1: OUTLINE VIEW */}
          {activeTab === "outline" && outline && (
            <div className="space-y-4">
              <div className="p-4 rounded-xl border border-border/70 bg-card space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="font-serif-academic font-bold text-sm text-foreground">
                    规划概览：{outline.objective}
                  </h3>
                  <Badge variant="secondary" className="font-mono text-xs">
                    预估篇幅: {outline.target_word_count_range?.[0]} ~ {outline.target_word_count_range?.[1]} 字
                  </Badge>
                </div>
                {outline.background_summary && (
                  <p className="text-xs text-foreground/80 font-serif-academic leading-relaxed">
                    {outline.background_summary}
                  </p>
                )}
              </div>

              <div className="space-y-3">
                <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider font-mono">
                  章节规划与探针配置 ({outline.chapters?.length || 0} 个递进章节)
                </h4>
                {outline.chapters?.map((ch) => (
                  <div
                    key={ch.chapter_id}
                    className="p-4 rounded-xl border border-border/80 bg-background space-y-2.5 shadow-2xs"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <span className="font-mono text-xs text-emerald-800 dark:text-emerald-400 font-bold mr-2">
                          Chapter {ch.chapter_index}
                        </span>
                        <span className="font-serif-academic font-bold text-sm text-foreground">
                          {ch.title}
                        </span>
                      </div>
                      {ch.depends_on && ch.depends_on.length > 0 && (
                        <Badge variant="outline" className="font-mono text-[10px]">
                          依赖: {ch.depends_on.join(", ")}
                        </Badge>
                      )}
                    </div>

                    <p className="text-xs text-foreground/80 font-serif-academic bg-muted/30 p-2.5 rounded border border-border/40">
                      <span className="font-semibold text-foreground">核心论题：</span>
                      {ch.core_thesis}
                    </p>

                    {ch.probes && ch.probes.length > 0 && (
                      <div className="space-y-1.5 pt-1">
                        <span className="text-[11px] font-bold text-muted-foreground uppercase font-mono">
                          微观探针 ({ch.probes.length} 个)
                        </span>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                          {ch.probes.map((p, pIdx) => (
                            <div
                              key={pIdx}
                              className="text-xs p-2 rounded bg-card border border-border/60 space-y-1"
                            >
                              <div className="font-medium text-foreground text-[11px]">
                                🎯 {p.target_aspect}
                              </div>
                              <div className="text-[11px] text-muted-foreground font-mono truncate">
                                意图: {p.query_intent}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB 2: REPORT FULL VIEW */}
          {activeTab === "report" && report && (
            <div className="space-y-6">
              {/* Executive Summary */}
              {report.executive_summary && report.executive_summary.length > 0 && (
                <div className="p-4 rounded-xl border border-emerald-800/30 bg-emerald-900/5 space-y-2">
                  <h4 className="font-serif-academic font-bold text-sm text-emerald-800 dark:text-emerald-300 flex items-center gap-1.5">
                    <Sparkles className="h-4 w-4" />
                    <span>执行摘要与核心结论 (Executive Summary)</span>
                  </h4>
                  <ul className="list-disc list-inside space-y-1 text-xs text-foreground/90 font-serif-academic">
                    {report.executive_summary.map((point, idx) => (
                      <li key={idx} className="leading-relaxed">
                        {point}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Chapter Sections */}
              <div className="space-y-5">
                {report.sections?.map((sec) => (
                  <div
                    key={sec.chapter_id}
                    className="p-5 rounded-xl border border-border/80 bg-card space-y-3 shadow-2xs"
                  >
                    <div className="flex items-center justify-between border-b border-border/40 pb-2">
                      <h3 className="font-serif-academic font-bold text-base text-foreground">
                        {sec.title}
                      </h3>
                      <div className="flex items-center space-x-2">
                        <Badge variant="outline" className="font-mono text-xs">
                          {sec.word_count} 字
                        </Badge>
                        <Badge variant="secondary" className="font-mono text-xs">
                          {sec.cited_source_urls?.length || 0} 信源
                        </Badge>
                      </div>
                    </div>

                    <div
                      className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                      dangerouslySetInnerHTML={{
                        __html: renderMarkdownWithMath(sec.content_markdown),
                      }}
                    />

                    {sec.cited_source_urls && sec.cited_source_urls.length > 0 && (
                      <div className="pt-2 border-t border-border/40 flex flex-wrap items-center gap-1.5">
                        <span className="text-[11px] font-mono text-muted-foreground">本章引用信源：</span>
                        {sec.cited_source_urls.map((url, uIdx) => (
                          <a
                            key={uIdx}
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-[11px] font-mono text-emerald-700 dark:text-emerald-400 hover:underline flex items-center gap-0.5 bg-muted/60 px-1.5 py-0.5 rounded"
                          >
                            <span>信源 {uIdx + 1}</span>
                            <ExternalLink className="h-2.5 w-2.5" />
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB 3: TIMELINE & MATRIX */}
          {activeTab === "timeline" && report && (
            <div className="space-y-5">
              {report.evolution_timeline_mermaid && (
                <div className="p-4 rounded-xl border border-border/80 bg-card space-y-2">
                  <h4 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                    <GitBranch className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                    <span>演化脉络时序图 (Mermaid Timeline)</span>
                  </h4>
                  <pre className="p-3 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-foreground/90 whitespace-pre-wrap">
                    {report.evolution_timeline_mermaid}
                  </pre>
                </div>
              )}

              {report.comparison_matrix_markdown && (
                <div className="p-4 rounded-xl border border-border/80 bg-card space-y-2">
                  <h4 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                    <Layers className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                    <span>多维架构演进对比矩阵 (Comparison Matrix)</span>
                  </h4>
                  <div
                    className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none overflow-x-auto"
                    dangerouslySetInnerHTML={{
                      __html: renderMarkdownWithMath(report.comparison_matrix_markdown),
                    }}
                  />
                </div>
              )}
            </div>
          )}

          {/* TAB 4: CITATIONS LEDGER */}
          {activeTab === "ledger" && report && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-serif-academic font-bold text-sm text-foreground">
                  正文实引台账清单 ({report.citations_ledger?.length || 0} 条严格引用)
                </span>
                <span className="text-xs text-muted-foreground font-mono">
                  严格过滤：仅收录正文实际引用的学术文献
                </span>
              </div>

              <div className="border border-border/70 rounded-xl overflow-hidden bg-card">
                <table className="w-full text-xs text-left">
                  <thead className="bg-muted/60 border-b border-border/60 text-muted-foreground font-mono">
                    <tr>
                      <th className="py-2.5 px-3 font-semibold">#</th>
                      <th className="py-2.5 px-3 font-semibold">文献标题</th>
                      <th className="py-2.5 px-3 font-semibold">权威度</th>
                      <th className="py-2.5 px-3 font-semibold">检索通道</th>
                      <th className="py-2.5 px-3 font-semibold">原文链接</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40 font-serif-academic">
                    {report.citations_ledger?.map((item, idx) => (
                      <tr key={idx} className="hover:bg-muted/30">
                        <td className="py-2.5 px-3 font-mono font-medium text-muted-foreground">
                          {idx + 1}
                        </td>
                        <td className="py-2.5 px-3 font-medium text-foreground max-w-md">
                          {item.title}
                        </td>
                        <td className="py-2.5 px-3 font-mono">
                          <Badge variant="secondary" className="text-[10px]">
                            {item.authority_score !== undefined
                              ? `${Math.round(item.authority_score * 100)}%`
                              : "N/A"}
                          </Badge>
                        </td>
                        <td className="py-2.5 px-3 font-mono text-[11px] text-muted-foreground">
                          {item.source_channel || "academic"}
                        </td>
                        <td className="py-2.5 px-3">
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-emerald-700 dark:text-emerald-400 hover:underline flex items-center gap-1 font-mono text-[11px]"
                          >
                            <span className="truncate max-w-[180px]">{item.url}</span>
                            <ExternalLink className="h-3 w-3 shrink-0" />
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
