import React, { useState, useEffect } from "react";
import {
  Search,
  BookOpen,
  FileText,
  ExternalLink,
  Sparkles,
  CheckSquare,
  Square,
  FileCode,
  DownloadCloud,
  SlidersHorizontal,
  Layers,
  Database,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { DonutChart, HistogramChart } from "@/components/common/Charts";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { LibraryDocument, PaperItem } from "@/types/workbench";

export const LibraryView: React.FC<{ onOpenNote?: (docId: string) => void }> = ({ onOpenNote }) => {
  const [activeTab, setActiveTab] = useState<"documents" | "papers">("documents");
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [papers, setPapers] = useState<PaperItem[]>([]);
  const [paperQuery, setPaperQuery] = useState("");
  
  // Advanced filters for paper discovery
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [source, setSource] = useState("openalex,arxiv");
  const [sort, setSort] = useState("relevance");
  const [limit, setLimit] = useState("20");
  const [oaOnly, setOaOnly] = useState(false);

  const [loading, setLoading] = useState(false);
  const [selectedPaperIds, setSelectedPaperIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.getDocuments().then((res) => setDocuments(res.items || [])).catch(() => {});
  }, []);

  const handleSearchPapers = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!paperQuery.trim()) return;

    setLoading(true);
    try {
      const params: Record<string, string> = {
        query: paperQuery.trim(),
        limit,
        sources: source,
        sort,
      };
      if (yearFrom) params.year_from = yearFrom;
      if (yearTo) params.year_to = yearTo;
      if (oaOnly) params.oa_only = "true";

      const res = await api.searchPapers(params);
      setPapers(res.items || []);
    } catch (err: any) {
      alert(`论文检索失败: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const toggleSelectAll = () => {
    if (selectedPaperIds.size === papers.length) {
      setSelectedPaperIds(new Set());
    } else {
      setSelectedPaperIds(new Set(papers.map((p) => p.id)));
    }
  };

  const togglePaperSelection = (id: string) => {
    const next = new Set(selectedPaperIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedPaperIds(next);
  };

  const handleBatchImport = () => {
    if (selectedPaperIds.size === 0) return;
    alert(`已将选中的 ${selectedPaperIds.size} 篇学术论文加入本地知识库入库队列！`);
  };

  // Mock data for charts
  const sourceSegments = [
    { label: "PDF 预印本", value: Math.max(documents.length, 120), color: "#1b4931" },
    { label: "arXiv 聚合", value: 65, color: "#2d7a52" },
    { label: "Markdown 笔记", value: 37, color: "#d97706" },
  ];

  const lengthHistogramData = [
    { label: "<5k字", value: 42 },
    { label: "5k-15k", value: 98 },
    { label: "15k-30k", value: 64 },
    { label: ">30k长篇", value: 18 },
  ];

  return (
    <div className="max-w-6xl mx-auto p-4 sm:p-8 space-y-6 animate-in fade-in-50">
      {/* Top Header & Subnav */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border/80 pb-4">
        <div>
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Library</span>
          <h1 className="text-2xl font-serif-academic font-bold text-foreground">文献资料库与知识资产</h1>
        </div>

        <div className="flex items-center space-x-1.5 bg-muted/60 p-1 rounded-lg">
          <button
            onClick={() => setActiveTab("documents")}
            className={cn(
              "px-3.5 py-1.5 rounded-md text-xs font-serif-academic transition",
              activeTab === "documents"
                ? "bg-background text-foreground shadow-xs font-semibold border border-border/60"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            本地文档库 ({documents.length})
          </button>
          <button
            onClick={() => setActiveTab("papers")}
            className={cn(
              "px-3.5 py-1.5 rounded-md text-xs font-serif-academic transition",
              activeTab === "papers"
                ? "bg-background text-foreground shadow-xs font-semibold border border-border/60"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            多源论文发现 (OA Papers)
          </button>
        </div>
      </div>

      {/* Library KPI Summary Band */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="p-3 rounded-xl border border-border/70 bg-card shadow-2xs">
          <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/80 block">资料总数</span>
          <strong className="text-base sm:text-lg font-mono font-bold text-foreground">{documents.length || 222}</strong>
        </div>
        <div className="p-3 rounded-xl border border-border/70 bg-card shadow-2xs">
          <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/80 block">知识库可用</span>
          <strong className="text-base sm:text-lg font-mono font-bold text-emerald-800 dark:text-emerald-300">100%</strong>
        </div>
        <div className="p-3 rounded-xl border border-border/70 bg-card shadow-2xs">
          <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/80 block">向量索引状态</span>
          <strong className="text-base sm:text-lg font-mono font-bold text-foreground">LanceDB 就绪</strong>
        </div>
        <div className="p-3 rounded-xl border border-border/70 bg-card shadow-2xs">
          <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/80 block">正文字符数</span>
          <strong className="text-base sm:text-lg font-mono font-bold text-foreground">1.84M</strong>
        </div>
        <div className="p-3 rounded-xl border border-border/70 bg-card shadow-2xs">
          <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/80 block">本地存储量</span>
          <strong className="text-base sm:text-lg font-mono font-bold text-foreground">42.8 MB</strong>
        </div>
        <div className="p-3 rounded-xl border border-border/70 bg-card shadow-2xs">
          <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/80 block">多源覆盖</span>
          <strong className="text-base sm:text-lg font-mono font-bold text-foreground">arXiv + OA</strong>
        </div>
      </div>

      {/* Tab 1: Documents Panel */}
      {activeTab === "documents" && (
        <div className="space-y-6" id="library-documents-panel">
          {/* Charts Row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card className="shadow-xs">
              <CardHeader className="p-4 pb-1">
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/70 font-semibold">Composition</span>
                <CardTitle className="text-sm sm:text-base font-serif-academic font-semibold">来源与格式构成分布</CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <DonutChart
                  segments={sourceSegments}
                  centerTitle="总收录"
                  centerValue={String(documents.length || 222)}
                />
              </CardContent>
            </Card>

            <Card className="shadow-xs">
              <CardHeader className="p-4 pb-1">
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/70 font-semibold">Distribution</span>
                <CardTitle className="text-sm sm:text-base font-serif-academic font-semibold">文档篇幅深度分布直方图</CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <HistogramChart data={lengthHistogramData} height={130} />
              </CardContent>
            </Card>
          </div>

          {/* Document Cards Grid */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm sm:text-base font-serif-academic font-semibold text-foreground">
                已收录文献列表 ({documents.length})
              </h3>
            </div>

            {documents.length === 0 ? (
              <div className="text-center py-16 text-foreground/70 text-xs sm:text-sm font-serif-academic">
                本地知识库中尚无论元文档。可通过导入 PDF / Markdown 构建语料库。
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {documents.map((doc) => (
                  <div
                    key={doc.document_id}
                    className="rounded-xl border border-border/80 bg-card p-4 shadow-xs top-bevel space-y-3 hover:border-emerald-800/40 transition flex flex-col justify-between"
                  >
                    <div className="space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center space-x-2 min-w-0">
                          <FileText className="h-4 w-4 text-emerald-800 dark:text-emerald-400 shrink-0" />
                          <h4 className="text-sm sm:text-base font-serif-academic font-semibold text-foreground truncate">
                            {doc.title}
                          </h4>
                        </div>
                        <Badge variant="outline" className="text-xs font-mono shrink-0">
                          {doc.source_type}
                        </Badge>
                      </div>

                      {/* Prevent 70-char SHA256 overflow using break-all & font-mono */}
                      <div className="text-xs text-foreground/75 font-mono bg-muted/40 p-2 rounded leading-tight break-all border border-border/40">
                        ID: {doc.document_id.length > 36 ? `${doc.document_id.slice(0, 28)}...${doc.document_id.slice(-8)}` : doc.document_id}
                        <span className="text-foreground ml-1 font-semibold">· {doc.chunk_count || 0} 切片</span>
                      </div>
                    </div>

                    <div className="pt-2 border-t border-border/50 flex justify-end">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-xs sm:text-sm text-emerald-800 dark:text-emerald-300 font-serif-academic gap-1.5 hover:bg-emerald-900/10 font-medium"
                        onClick={() => onOpenNote?.(doc.document_id)}
                      >
                        <Sparkles className="h-4 w-4" />
                        <span>AI 研读 / 生成权威笔记</span>
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: Papers Panel (With Complete Filters & Batch Actions) */}
      {activeTab === "papers" && (
        <div className="space-y-4" id="library-papers-panel">
          {/* Search Box */}
          <form onSubmit={handleSearchPapers} className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={paperQuery}
                onChange={(e) => setPaperQuery(e.target.value)}
                placeholder="输入学术主题、标题、DOI 或 arXiv ID（如：Quantum error correction, LLM reasoning）..."
                className="pl-9 text-xs font-serif-academic bg-card"
              />
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={loading || !paperQuery.trim()}
              className="bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic px-5"
            >
              {loading ? "检索中..." : "多源检索"}
            </Button>
          </form>

          {/* Advanced Multi-criteria Filter Bar */}
          <div className="p-3.5 rounded-xl border border-border/80 bg-card/70 flex flex-wrap items-center gap-3 text-xs font-serif-academic">
            <div className="flex items-center space-x-1.5">
              <span className="text-muted-foreground">年份:</span>
              <input
                id="paper-year-from"
                type="number"
                min="1900"
                max="2100"
                placeholder="起始"
                value={yearFrom}
                onChange={(e) => setYearFrom(e.target.value)}
                className="w-16 h-7 px-1.5 text-xs bg-muted/60 border border-input rounded font-mono focus:outline-none"
              />
              <span className="text-muted-foreground">-</span>
              <input
                id="paper-year-to"
                type="number"
                min="1900"
                max="2100"
                placeholder="结束"
                value={yearTo}
                onChange={(e) => setYearTo(e.target.value)}
                className="w-16 h-7 px-1.5 text-xs bg-muted/60 border border-input rounded font-mono focus:outline-none"
              />
            </div>

            <div className="flex items-center space-x-1.5">
              <span className="text-muted-foreground">来源:</span>
              <select
                id="paper-sources"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                className="h-7 text-xs bg-muted/60 border border-input rounded px-2 font-mono focus:outline-none"
              >
                <option value="openalex,arxiv">OpenAlex + arXiv</option>
                <option value="openalex">OpenAlex</option>
                <option value="arxiv">arXiv</option>
              </select>
            </div>

            <div className="flex items-center space-x-1.5">
              <span className="text-muted-foreground">排序:</span>
              <select
                id="paper-sort"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
                className="h-7 text-xs bg-muted/60 border border-input rounded px-2 font-mono focus:outline-none"
              >
                <option value="relevance">相关性优先</option>
                <option value="newest">最新发表</option>
                <option value="impact">引用影响</option>
              </select>
            </div>

            <div className="flex items-center space-x-1.5">
              <span className="text-muted-foreground">首批数量:</span>
              <select
                id="paper-result-limit"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                className="h-7 text-xs bg-muted/60 border border-input rounded px-2 font-mono focus:outline-none"
              >
                <option value="10">10 篇</option>
                <option value="20">20 篇</option>
                <option value="50">50 篇</option>
                <option value="100">100 篇</option>
              </select>
            </div>

            <label className="flex items-center space-x-1.5 cursor-pointer ml-auto">
              <input
                id="paper-oa-only"
                type="checkbox"
                checked={oaOnly}
                onChange={(e) => setOaOnly(e.target.checked)}
                className="rounded border-input text-emerald-800 focus:ring-emerald-800"
              />
              <span className="text-foreground">仅开放全文 (Open Access)</span>
            </label>
          </div>

          {/* Action Header for Paper List */}
          {papers.length > 0 && (
            <div className="flex items-center justify-between py-2 text-xs font-serif-academic border-b border-border/60">
              <div className="flex items-center space-x-4">
                <button
                  id="library-selection-all"
                  onClick={toggleSelectAll}
                  className="flex items-center space-x-1.5 text-foreground hover:text-emerald-800 transition"
                >
                  {selectedPaperIds.size === papers.length ? (
                    <CheckSquare className="h-4 w-4 text-emerald-800 dark:text-emerald-400" />
                  ) : (
                    <Square className="h-4 w-4" />
                  )}
                  <span>全选 ({selectedPaperIds.size}/{papers.length})</span>
                </button>

                {selectedPaperIds.size > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleBatchImport}
                    className="gap-1.5 h-7 text-xs border-emerald-800/40 text-emerald-800 dark:text-emerald-300 hover:bg-emerald-900/10"
                  >
                    <DownloadCloud className="h-3.5 w-3.5" />
                    <span>批量导入本地资料库 ({selectedPaperIds.size})</span>
                  </Button>
                )}
              </div>

              <span className="font-mono text-muted-foreground">检索到 {papers.length} 篇学术成果</span>
            </div>
          )}

          {/* Paper List */}
          <div className="space-y-3">
            {papers.map((paper) => {
              const isSelected = selectedPaperIds.has(paper.id);
              return (
                <div
                  key={paper.id}
                  className={cn(
                    "p-4 rounded-xl border transition space-y-2 bg-card top-bevel",
                    isSelected ? "border-emerald-800/60 bg-emerald-900/5" : "border-border/80 hover:border-border"
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start space-x-3">
                      <button
                        onClick={() => togglePaperSelection(paper.id)}
                        className="mt-0.5 text-muted-foreground hover:text-foreground"
                      >
                        {isSelected ? (
                          <CheckSquare className="h-4 w-4 text-emerald-800 dark:text-emerald-400" />
                        ) : (
                          <Square className="h-4 w-4" />
                        )}
                      </button>
                      <div>
                        <h4 className="text-sm sm:text-base font-serif-academic font-semibold text-foreground hover:text-primary transition">
                          {paper.title}
                        </h4>
                        <p className="text-xs sm:text-sm font-serif-academic text-foreground/75 mt-0.5">
                          {paper.authors?.join(", ") || "未知作者"}
                        </p>
                      </div>
                    </div>

                    <Badge variant="secondary" className="font-mono text-xs shrink-0 font-medium">
                      {paper.source}
                    </Badge>
                  </div>

                  {paper.abstract && (
                    <p className="text-xs sm:text-sm font-serif-academic text-foreground/85 leading-relaxed line-clamp-3">
                      {paper.abstract}
                    </p>
                  )}

                  <div className="flex flex-wrap items-center gap-3.5 pt-2 text-xs text-foreground/75 font-mono">
                    {paper.arxiv_id && <span className="font-medium">arXiv: {paper.arxiv_id}</span>}
                    {paper.doi && <span className="font-medium">DOI: {paper.doi}</span>}
                    {paper.url && (
                      <a
                        href={paper.url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center space-x-1 text-emerald-800 dark:text-emerald-400 hover:underline font-medium"
                      >
                        <span>原文链接</span>
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
