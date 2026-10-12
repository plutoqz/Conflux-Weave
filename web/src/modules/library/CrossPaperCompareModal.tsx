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
  Scale,
  GitBranch,
  Layers,
  FileText,
  Copy,
  Check,
  Download,
} from "lucide-react";
import { api } from "@/services/api";
import { renderMarkdownWithMath } from "@/lib/math";
import type { PaperItem, CrossPaperCompareResponse } from "@/types/workbench";

interface CrossPaperCompareModalProps {
  papers: PaperItem[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const CrossPaperCompareModal: React.FC<CrossPaperCompareModalProps> = ({
  papers,
  open,
  onOpenChange,
}) => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<CrossPaperCompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"entities" | "matrix" | "timeline" | "synthesis">("matrix");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (open && papers.length >= 2) {
      handleCompare();
    }
  }, [open, papers]);

  const handleCompare = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.compareCrossPapers({
        papers: papers.map((p) => ({
          paper_id: p.id,
          title: p.title,
          raw_text: p.abstract || undefined,
          year: p.published_year || undefined,
        })),
      });
      setData(res);
      setActiveTab("matrix");
    } catch (err: any) {
      setError(err.message || "跨论文对比分析失败");
    } finally {
      setLoading(false);
    }
  };

  const handleCopyMarkdown = () => {
    if (!data) return;
    const fullText = `# 跨论文对比分析报告\n\n## 对比矩阵\n\n${data.dimension_matrix_markdown}\n\n## 演化时序\n\n\`\`\`mermaid\n${data.lineage_mermaid_timeline}\n\`\`\`\n\n## 综合综述\n\n${data.synthesis_narrative}`;
    navigator.clipboard.writeText(fullText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[92vh] flex flex-col p-6 overflow-hidden">
        <DialogHeader className="shrink-0 border-b border-border/60 pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2.5">
              <Scale className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />
              <DialogTitle className="text-base font-serif-academic font-bold">
                跨论文范式与架构机理对比 (Cross-Paper Comparison)
              </DialogTitle>
            </div>
            <Badge variant="secondary" className="font-mono text-xs">
              已选 {papers.length} 篇学术成果
            </Badge>
          </div>
          <DialogDescription className="text-xs text-muted-foreground font-serif-academic pt-1">
            横向对齐核心问题、架构机理、算力空间开销、基准测试效果与设计权衡。
          </DialogDescription>
        </DialogHeader>

        {/* Tab Switcher & Actions */}
        <div className="shrink-0 flex items-center justify-between border-b border-border/50 py-2.5">
          <div className="flex items-center space-x-1.5 bg-muted/60 p-1 rounded-lg text-xs font-serif-academic">
            <button
              type="button"
              onClick={() => setActiveTab("matrix")}
              className={`px-3 py-1 rounded-md font-medium transition ${
                activeTab === "matrix"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              📊 多维对比矩阵
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("entities")}
              className={`px-3 py-1 rounded-md font-medium transition ${
                activeTab === "entities"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              📄 论文实体剖析
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("timeline")}
              className={`px-3 py-1 rounded-md font-medium transition ${
                activeTab === "timeline"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              🌿 演进时序拓扑
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("synthesis")}
              className={`px-3 py-1 rounded-md font-medium transition ${
                activeTab === "synthesis"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              📝 综合归纳研判
            </button>
          </div>

          <div className="flex items-center space-x-2">
            <Button
              size="sm"
              variant="outline"
              onClick={handleCompare}
              disabled={loading}
              className="text-xs font-serif-academic h-8"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "重新对比"}
            </Button>
            {data && (
              <Button
                size="sm"
                variant="outline"
                onClick={handleCopyMarkdown}
                className="text-xs font-serif-academic h-8 gap-1"
              >
                {copied ? <Check className="h-3 w-3 text-emerald-600" /> : <Copy className="h-3 w-3" />}
                <span>{copied ? "已复制" : "复制报告"}</span>
              </Button>
            )}
          </div>
        </div>

        {error && (
          <div className="shrink-0 text-xs text-rose-600 bg-rose-500/10 p-2.5 rounded border border-rose-500/20 my-2">
            {error}
          </div>
        )}

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto pt-2 space-y-4">
          {loading ? (
            <div className="h-64 flex flex-col items-center justify-center text-center">
              <Loader2 className="h-8 w-8 animate-spin text-emerald-700 dark:text-emerald-400 mb-3" />
              <p className="text-sm font-serif-academic font-semibold text-foreground">
                正在对齐各论文关键算子、实证基准并生成多维对比矩阵...
              </p>
            </div>
          ) : data ? (
            <>
              {/* TAB 1: MATRIX */}
              {activeTab === "matrix" && (
                <div className="p-4 rounded-xl border border-border/80 bg-card space-y-3 shadow-2xs">
                  <h4 className="font-serif-academic font-bold text-sm text-foreground">
                    多维关键指标与架构机制横向矩阵
                  </h4>
                  <div
                    className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none overflow-x-auto"
                    dangerouslySetInnerHTML={{
                      __html: renderMarkdownWithMath(data.dimension_matrix_markdown),
                    }}
                  />
                </div>
              )}

              {/* TAB 2: ENTITIES */}
              {activeTab === "entities" && (
                <div className="space-y-4">
                  {data.entities?.map((ent, idx) => (
                    <div
                      key={ent.paper_id || idx}
                      className="p-4 rounded-xl border border-border/80 bg-card space-y-3 shadow-2xs"
                    >
                      <div className="flex items-center justify-between border-b border-border/40 pb-2">
                        <div className="flex items-center space-x-2">
                          <span className="font-serif-academic font-bold text-sm text-foreground">
                            {ent.title}
                          </span>
                          {ent.year && (
                            <Badge variant="outline" className="font-mono text-xs">
                              {ent.year}
                            </Badge>
                          )}
                        </div>
                        <Badge className="font-serif-academic text-xs bg-emerald-800 text-white">
                          {ent.paradigm}
                        </Badge>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-serif-academic">
                        <div className="space-y-1 p-2 rounded bg-muted/20 border border-border/40">
                          <span className="font-semibold text-foreground">核心问题与假说：</span>
                          <p className="text-muted-foreground">{ent.core_problem}</p>
                        </div>
                        <div className="space-y-1 p-2 rounded bg-muted/20 border border-border/40">
                          <span className="font-semibold text-foreground">关键架构机制：</span>
                          <p className="text-muted-foreground">{ent.key_architecture_mechanism}</p>
                        </div>
                        <div className="space-y-1 p-2 rounded bg-muted/20 border border-border/40">
                          <span className="font-semibold text-foreground">计算/空间开销：</span>
                          <p className="text-muted-foreground">{ent.compute_space_overhead}</p>
                        </div>
                        <div className="space-y-1 p-2 rounded bg-muted/20 border border-border/40">
                          <span className="font-semibold text-foreground">主要基准测评指标：</span>
                          <p className="text-muted-foreground">{ent.main_benchmark_metrics}</p>
                        </div>
                      </div>

                      {ent.tradeoffs_and_risks && (
                        <div className="text-xs p-2.5 rounded bg-amber-500/10 border border-amber-500/20 text-amber-900 dark:text-amber-200 font-serif-academic">
                          <span className="font-semibold">设计取舍与潜在局限：</span>
                          {ent.tradeoffs_and_risks}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* TAB 3: TIMELINE */}
              {activeTab === "timeline" && (
                <div className="p-4 rounded-xl border border-border/80 bg-card space-y-2">
                  <h4 className="font-serif-academic font-bold text-xs text-foreground flex items-center gap-1.5">
                    <GitBranch className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                    <span>学术谱系演进时序图 (Mermaid Lineage Timeline)</span>
                  </h4>
                  <pre className="p-3 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-foreground/90 whitespace-pre-wrap">
                    {data.lineage_mermaid_timeline}
                  </pre>
                </div>
              )}

              {/* TAB 4: SYNTHESIS */}
              {activeTab === "synthesis" && (
                <div className="p-5 rounded-xl border border-border/80 bg-card shadow-2xs">
                  <div
                    className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                    dangerouslySetInnerHTML={{
                      __html: renderMarkdownWithMath(data.synthesis_narrative),
                    }}
                  />
                </div>
              )}
            </>
          ) : (
            <div className="h-64 flex flex-col items-center justify-center text-center text-muted-foreground p-8">
              <Scale className="h-10 w-10 text-muted-foreground/40 mb-3" />
              <p className="text-sm font-serif-academic font-medium text-foreground/80">
                选择两篇及以上论文进行跨篇横向对比
              </p>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
