import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Loader2,
  BookOpen,
  GitFork,
  Image as ImageIcon,
  ExternalLink,
  Sparkles,
  Layers,
  FileText,
  Copy,
  Check,
  Download,
} from "lucide-react";
import { api } from "@/services/api";
import { renderMarkdownWithMath } from "@/lib/math";
import type {
  PaperItem,
  PaperClassifyResponse,
  PaperCitationGraphResponse,
  PaperReadingV2ReportResponse,
} from "@/types/workbench";

interface PaperReadingV2ModalProps {
  paper: PaperItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const PaperReadingV2Modal: React.FC<PaperReadingV2ModalProps> = ({
  paper,
  open,
  onOpenChange,
}) => {
  const [activeTab, setActiveTab] = useState<"classify" | "citation" | "reading">("classify");
  const [loadingClassify, setLoadingClassify] = useState(false);
  const [loadingCitation, setLoadingCitation] = useState(false);
  const [loadingReading, setLoadingReading] = useState(false);

  const [classifyData, setClassifyData] = useState<PaperClassifyResponse | null>(null);
  const [citationData, setCitationData] = useState<PaperCitationGraphResponse | null>(null);
  const [readingData, setReadingData] = useState<PaperReadingV2ReportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [pdfPath, setPdfPath] = useState<string>("");

  useEffect(() => {
    if (open && paper) {
      setClassifyData(null);
      setCitationData(null);
      setReadingData(null);
      setError(null);
      setActiveTab("classify");
      const defaultPdf = paper.pdf_path || (paper.url?.toLowerCase().endsWith(".pdf") ? paper.url : (paper.abstract?.toLowerCase().endsWith(".pdf") ? paper.abstract : ""));
      setPdfPath(defaultPdf);
      // Auto run classify on modal open
      handleClassify();
    }
  }, [open, paper?.id]);

  if (!paper) return null;

  const handleClassify = async () => {
    if (!paper) return;
    setLoadingClassify(true);
    setError(null);
    try {
      const res = await api.classifyPaperParadigm({
        title: paper.title,
        abstract: paper.abstract || undefined,
        section_headings: ["Introduction", "Related Work", "Method", "Experiments", "Discussion"],
      });
      setClassifyData(res);
      setActiveTab("classify");
    } catch (err: any) {
      setError(err.message || "范式自适应识别失败");
    } finally {
      setLoadingClassify(false);
    }
  };

  const handleCitationGraph = async () => {
    if (!paper) return;
    setLoadingCitation(true);
    setError(null);
    try {
      const res = await api.getPaperCitationGraph({
        paper_id: paper.id,
        title: paper.title,
        raw_text: paper.abstract || undefined,
        doi: paper.doi || undefined,
        allow_network: true,
      });
      setCitationData(res);
      setActiveTab("citation");
    } catch (err: any) {
      setError(err.message || "构建双向引用图谱失败");
    } finally {
      setLoadingCitation(false);
    }
  };

  const handleExecuteReading = async () => {
    if (!paper) return;
    setLoadingReading(true);
    setError(null);
    try {
      const finalPdf = pdfPath.trim() || paper.pdf_path || (paper.url?.toLowerCase().endsWith(".pdf") ? paper.url : (paper.abstract?.toLowerCase().endsWith(".pdf") ? paper.abstract : undefined));
      const res = await api.executePaperReadingV2({
        paper_id: paper.id,
        title: paper.title,
        raw_text: paper.abstract || undefined,
        doi: paper.doi || undefined,
        pdf_path: finalPdf || undefined,
        allow_network: true,
      });
      setReadingData(res);
      setActiveTab("reading");
    } catch (err: any) {
      setError(err.message || "论文深度精读失败");
    } finally {
      setLoadingReading(false);
    }
  };

  const handleCopyMarkdown = () => {
    if (!readingData?.markdown_content) return;
    navigator.clipboard.writeText(readingData.markdown_content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadMarkdown = () => {
    if (!readingData?.markdown_content) return;
    const blob = new Blob([readingData.markdown_content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `PaperReading_${paper.title.slice(0, 30).replace(/[^\w\u4e00-\u9fa5]/g, "_")}.md`;
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
              <BookOpen className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />
              <DialogTitle className="text-base font-serif-academic font-bold truncate max-w-xl">
                {paper.title}
              </DialogTitle>
            </div>
            <div className="flex items-center space-x-2">
              {paper.arxiv_id && (
                <Badge variant="outline" className="font-mono text-xs">
                  arXiv: {paper.arxiv_id}
                </Badge>
              )}
              {paper.doi && (
                <Badge variant="outline" className="font-mono text-xs">
                  DOI: {paper.doi}
                </Badge>
              )}
            </div>
          </div>
          <DialogDescription className="text-xs text-muted-foreground font-serif-academic pt-1 truncate">
            {paper.authors?.join(", ") || "作者未知"} {paper.published_year ? `(${paper.published_year})` : ""}
          </DialogDescription>
        </DialogHeader>

        {/* Action & Tab Switcher Bar */}
        <div className="shrink-0 flex flex-wrap items-center justify-between gap-3 py-2.5 border-b border-border/50">
          <div className="flex items-center space-x-1.5 bg-muted/60 p-1 rounded-lg text-xs font-serif-academic">
            <button
              type="button"
              onClick={() => setActiveTab("classify")}
              className={`px-3 py-1 rounded-md font-medium transition ${
                activeTab === "classify"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              🧠 范式识别与蓝图
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("citation")}
              className={`px-3 py-1 rounded-md font-medium transition ${
                activeTab === "citation"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              🔗 双向引文图谱
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("reading")}
              className={`px-3 py-1 rounded-md font-medium transition ${
                activeTab === "reading"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              📖 深度精读与视觉解析
            </button>
          </div>

          <div className="flex items-center space-x-2">
            {activeTab === "classify" && (
              <Button
                size="sm"
                variant="outline"
                onClick={handleClassify}
                disabled={loadingClassify}
                className="text-xs font-serif-academic h-8"
              >
                {loadingClassify ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "重新识别范式"}
              </Button>
            )}
            {activeTab === "citation" && (
              <Button
                size="sm"
                variant="outline"
                onClick={handleCitationGraph}
                disabled={loadingCitation}
                className="text-xs font-serif-academic h-8"
              >
                {loadingCitation ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "分析双向引文"}
              </Button>
            )}
            {activeTab === "reading" && !readingData && (
              <Button
                size="sm"
                onClick={handleExecuteReading}
                disabled={loadingReading}
                className="text-xs font-serif-academic h-8 bg-emerald-800 hover:bg-emerald-900 text-white gap-1.5"
              >
                {loadingReading ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>精读生成中...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="h-3.5 w-3.5" />
                    <span>执行深度精读</span>
                  </>
                )}
              </Button>
            )}
            {readingData && (
              <div className="flex items-center space-x-1.5">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleCopyMarkdown}
                  className="text-xs font-serif-academic h-8 gap-1"
                >
                  {copied ? <Check className="h-3 w-3 text-emerald-600" /> : <Copy className="h-3 w-3" />}
                  <span>{copied ? "已复制" : "复制"}</span>
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleDownloadMarkdown}
                  className="text-xs font-serif-academic h-8 gap-1"
                >
                  <Download className="h-3 w-3" />
                  <span>导出</span>
                </Button>
              </div>
            )}
          </div>
        </div>

        {error && (
          <div className="shrink-0 text-xs text-rose-600 bg-rose-500/10 p-2.5 rounded border border-rose-500/20 my-2">
            {error}
          </div>
        )}

        {/* Scrollable Content Body */}
        <div className="flex-1 overflow-y-auto pt-2 space-y-4">
          {/* TAB 1: PARADIGM CLASSIFICATION */}
          {activeTab === "classify" && (
            <div className="space-y-4">
              {loadingClassify ? (
                <div className="h-48 flex flex-col items-center justify-center text-center">
                  <Loader2 className="h-7 w-7 animate-spin text-emerald-700 dark:text-emerald-400 mb-2" />
                  <span className="text-xs font-serif-academic text-muted-foreground">
                    正在分析论文标题、摘要与目录，自适应判定科研范式...
                  </span>
                </div>
              ) : classifyData ? (
                <>
                  <div className="p-4 rounded-xl border border-emerald-800/30 bg-emerald-900/5 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="text-xs font-semibold text-muted-foreground">科研文类范式：</span>
                        <Badge className="font-serif-academic text-xs bg-emerald-800 hover:bg-emerald-900 text-white">
                          {classifyData.paradigm}
                        </Badge>
                      </div>
                      <Badge variant="outline" className="font-mono text-xs">
                        置信度: {Math.round(classifyData.confidence * 100)}%
                      </Badge>
                    </div>
                    <p className="text-xs text-foreground/85 font-serif-academic leading-relaxed">
                      <span className="font-semibold text-foreground">判定依据：</span>
                      {classifyData.rationale}
                    </p>
                  </div>

                  <div className="space-y-3">
                    <h4 className="text-xs font-bold text-muted-foreground uppercase tracking-wider font-mono">
                      范式自适应精读蓝图 ({classifyData.chapters?.length || 0} 个目标章节)
                    </h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {classifyData.chapters?.map((ch, idx) => (
                        <div
                          key={idx}
                          className="p-3.5 rounded-xl border border-border/80 bg-card space-y-2 shadow-2xs"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-serif-academic font-bold text-xs text-foreground">
                              {idx + 1}. {ch.title}
                            </span>
                            <Badge variant="secondary" className="font-mono text-[10px]">
                              {ch.key}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground font-serif-academic leading-relaxed">
                            {ch.target_aspect}
                          </p>
                          {ch.expected_visual_types && ch.expected_visual_types.length > 0 && (
                            <div className="flex flex-wrap gap-1 pt-1">
                              {ch.expected_visual_types.map((v, vIdx) => (
                                <Badge key={vIdx} variant="outline" className="font-mono text-[10px] text-foreground/70">
                                  🖼️ {v}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <div className="h-48 flex flex-col items-center justify-center text-center text-muted-foreground">
                  <p className="text-xs font-serif-academic">点击上方【重新识别范式】开始解析</p>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: DUAL CITATION LINEAGE GRAPH */}
          {activeTab === "citation" && (
            <div className="space-y-4">
              {loadingCitation ? (
                <div className="h-48 flex flex-col items-center justify-center text-center">
                  <Loader2 className="h-7 w-7 animate-spin text-emerald-700 dark:text-emerald-400 mb-2" />
                  <span className="text-xs font-serif-academic text-muted-foreground">
                    正在检索开放学术网络，追溯前序基石文献与后续衍生工作...
                  </span>
                </div>
              ) : citationData ? (
                <>
                  {citationData.mermaid_graph && (
                    <div className="p-4 rounded-xl border border-border/80 bg-card space-y-2">
                      <h4 className="font-serif-academic font-bold text-xs text-foreground flex items-center gap-1.5">
                        <GitFork className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                        <span>学术脉络拓扑图 (Mermaid Lineage)</span>
                      </h4>
                      <pre className="p-3 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-foreground/90 whitespace-pre-wrap">
                        {citationData.mermaid_graph}
                      </pre>
                    </div>
                  )}

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* Backward Foundations */}
                    <div className="space-y-2.5">
                      <div className="flex items-center justify-between">
                        <h5 className="font-serif-academic font-bold text-xs text-foreground">
                          🏛️ 前序基石文献 ({citationData.backward_foundations?.length || 0} 篇)
                        </h5>
                      </div>
                      <div className="space-y-2">
                        {citationData.backward_foundations?.map((item, idx) => (
                          <div key={idx} className="p-3 rounded-lg border border-border/70 bg-card space-y-1">
                            <div className="font-serif-academic font-semibold text-xs text-foreground">
                              {item.title} {item.year ? `(${item.year})` : ""}
                            </div>
                            <div className="text-[11px] text-muted-foreground font-mono">
                              作者: {item.authors?.join(", ") || "未知"} {item.venue ? `· ${item.venue}` : ""}
                            </div>
                            <p className="text-xs text-foreground/80 font-serif-academic">
                              <span className="font-semibold">传承关系：</span>
                              {item.summary_or_context || item.citation_type}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Forward Derivatives */}
                    <div className="space-y-2.5">
                      <div className="flex items-center justify-between">
                        <h5 className="font-serif-academic font-bold text-xs text-foreground">
                          🌱 后续衍生工作 ({citationData.forward_derivatives?.length || 0} 篇)
                        </h5>
                      </div>
                      <div className="space-y-2">
                        {citationData.forward_derivatives && citationData.forward_derivatives.length > 0 ? (
                          citationData.forward_derivatives.map((item, idx) => (
                            <div key={idx} className="p-3 rounded-lg border border-border/70 bg-card space-y-1">
                              <div className="font-serif-academic font-semibold text-xs text-foreground">
                                {item.title} {item.year ? `(${item.year})` : ""}
                              </div>
                              <div className="text-[11px] text-muted-foreground font-mono">
                                作者: {item.authors?.join(", ") || "未知"}
                              </div>
                              <p className="text-xs text-foreground/80 font-serif-academic">
                                <span className="font-semibold">演进维度：</span>
                                {item.summary_or_context || item.citation_type}
                              </p>
                            </div>
                          ))
                        ) : (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50 text-xs text-muted-foreground font-serif-academic text-center">
                            本地离线模式或网络未检索到后续引用文献。严格依据真实证据，不生成虚假衍生论文。
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div className="h-48 flex flex-col items-center justify-center text-center text-muted-foreground">
                  <p className="text-xs font-serif-academic">点击上方【分析双向引文】开始构建引文源流</p>
                </div>
              )}
            </div>
          )}

          {/* TAB 3: DEEP READING REPORT & VISUAL SLICES */}
          {activeTab === "reading" && (
            <div className="space-y-6">
              {/* PDF Visual Slices Input & Status Bar */}
              <div className="p-3 rounded-lg border border-border/70 bg-muted/20 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2 text-xs font-serif-academic font-medium text-foreground">
                    <ImageIcon className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                    <span>本地 PDF 视觉切片引擎</span>
                  </div>
                  {pdfPath ? (
                    <Badge variant="secondary" className="text-[10px] text-emerald-700 dark:text-emerald-300 font-mono">
                      ✓ 已关联本地 PDF
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-[10px] text-muted-foreground font-mono">
                      纯文本解析模式
                    </Badge>
                  )}
                </div>
                <div className="flex items-center space-x-2">
                  <input
                    type="text"
                    value={pdfPath}
                    onChange={(e) => setPdfPath(e.target.value)}
                    placeholder="输入或确认 PDF 路径 (例如: var/corpus/attention.pdf)，将自动提取高清架构图/公式/图表"
                    className="w-full text-xs px-2.5 py-1.5 rounded-md border border-input bg-background font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                  {!readingData && (
                    <Button
                      size="sm"
                      onClick={handleExecuteReading}
                      disabled={loadingReading}
                      className="text-xs font-serif-academic shrink-0 bg-emerald-800 hover:bg-emerald-900 text-white"
                    >
                      {loadingReading ? "解析中..." : "开始精读"}
                    </Button>
                  )}
                </div>
              </div>
              {loadingReading ? (
                <div className="h-64 flex flex-col items-center justify-center text-center">
                  <Loader2 className="h-8 w-8 animate-spin text-emerald-700 dark:text-emerald-400 mb-3" />
                  <p className="text-sm font-serif-academic font-semibold text-foreground">
                    正在执行自适应精读，提取理论脉络、关键算子与实证数据...
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    如提供 PDF 原文，系统将同步裁剪提取高分辨率架构图与消融曲线。
                  </p>
                </div>
              ) : readingData ? (
                <>
                  {/* Visual Slices Section */}
                  {readingData.visual_slices && readingData.visual_slices.length > 0 && (
                    <div className="space-y-3 p-4 rounded-xl border border-border/80 bg-muted/20">
                      <h4 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                        <ImageIcon className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                        <span>论文关键图表与实证消融切片 ({readingData.visual_slices.length} 张原图提取)</span>
                      </h4>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {readingData.visual_slices.map((slice, sIdx) => (
                          <div
                            key={slice.asset_id || sIdx}
                            className="rounded-xl border border-border/80 bg-card overflow-hidden shadow-2xs space-y-2 p-3"
                          >
                            <div className="flex items-center justify-between pb-1 border-b border-border/40 text-[11px] font-mono">
                              <Badge variant="outline">Page {slice.page}</Badge>
                              <Badge variant="secondary">{slice.asset_type}</Badge>
                            </div>
                            <div className="bg-muted/40 rounded-lg p-2 flex items-center justify-center min-h-[160px]">
                              <img
                                src={`/api/v1/assets/${slice.asset_id}`}
                                alt={slice.caption || "Paper Visual Slice"}
                                className="max-h-60 max-w-full object-contain rounded"
                                onError={(e) => {
                                  // Fallback display if asset load failed
                                  (e.target as HTMLElement).style.display = "none";
                                }}
                              />
                            </div>
                            <div className="space-y-1 pt-1">
                              <p className="text-xs font-serif-academic font-medium text-foreground">
                                {slice.caption}
                              </p>
                              {slice.deep_analysis && (
                                <p className="text-xs text-foreground/80 font-serif-academic leading-relaxed bg-muted/30 p-2 rounded">
                                  <span className="font-semibold text-foreground">图表研读：</span>
                                  {slice.deep_analysis}
                                </p>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Reading Markdown Content */}
                  <div className="p-5 rounded-xl border border-border/80 bg-card shadow-2xs">
                    <div
                      className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                      dangerouslySetInnerHTML={{
                        __html: renderMarkdownWithMath(readingData.markdown_content),
                      }}
                    />
                  </div>
                </>
              ) : (
                <div className="h-64 flex flex-col items-center justify-center text-center text-muted-foreground p-8">
                  <BookOpen className="h-10 w-10 text-muted-foreground/40 mb-3" />
                  <p className="text-sm font-serif-academic font-medium text-foreground/80">
                    点击【执行深度精读】生成结构化研读报告
                  </p>
                  <p className="text-xs text-muted-foreground max-w-md mt-1">
                    系统将依据文类范式蓝图，逐章节深度解析核心机理、数学公式、基准实验与开放挑战。
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
