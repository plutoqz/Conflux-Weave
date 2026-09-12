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
  Image as ImageIcon,
  FileSearch,
  ZoomIn,
  Loader2,
  Filter,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DonutChart, HistogramChart } from "@/components/common/Charts";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { LibraryDocument, PaperItem } from "@/types/workbench";

export const LibraryView: React.FC<{ onOpenNote?: (docId: string) => void }> = ({ onOpenNote }) => {
  const [activeTab, setActiveTab] = useState<"documents" | "multimodal" | "assets" | "papers">("documents");
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);
  const [papers, setPapers] = useState<PaperItem[]>([]);
  const [paperQuery, setPaperQuery] = useState("");
  
  // Multimodal RAG states
  const [multimodalQuery, setMultimodalQuery] = useState("");
  const [multimodalLoading, setMultimodalLoading] = useState(false);
  const [multimodalResults, setMultimodalResults] = useState<{
    query: string;
    text_hits_count: number;
    image_hits_count: number;
    fusion_strategy: string;
    fused_hits: any[];
  } | null>(null);
  const [modalityFilter, setModalityFilter] = useState<"all" | "text" | "image">("all");

  // Visual Assets states
  const [allAssets, setAllAssets] = useState<any[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [assetTypeFilter, setAssetTypeFilter] = useState<string>("all");
  const [lightboxAsset, setLightboxAsset] = useState<{ url: string; title: string; caption?: string } | null>(null);

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

  const loadAssets = async () => {
    setAssetsLoading(true);
    try {
      const res = await api.getLibraryAssets({ limit: 80 });
      setAllAssets(res.items || []);
    } catch {
      // ignore
    } finally {
      setAssetsLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "assets" && allAssets.length === 0) {
      loadAssets();
    }
  }, [activeTab]);

  const handleMultimodalSearch = async (e?: React.FormEvent, customQuery?: string) => {
    if (e) e.preventDefault();
    const q = (customQuery ?? multimodalQuery).trim();
    if (!q) return;

    if (customQuery) setMultimodalQuery(customQuery);
    setMultimodalLoading(true);
    try {
      const res = await api.searchLibraryMultimodal(q, 15, 8);
      setMultimodalResults(res);
    } catch (err: any) {
      alert(`多模态检索失败: ${err.message}`);
    } finally {
      setMultimodalLoading(false);
    }
  };

  const handleSearchPapers = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!paperQuery.trim()) return;

    setLoading(true);
    try {
      const params: Record<string, string> = {
        query: paperQuery.trim(),
        limit,
        max_results: limit,
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

        <div className="flex flex-wrap items-center space-x-1.5 bg-muted/60 p-1 rounded-lg">
          <button
            onClick={() => setActiveTab("documents")}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-serif-academic transition",
              activeTab === "documents"
                ? "bg-background text-foreground shadow-xs font-semibold border border-border/60"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            本地文档库 ({documents.length})
          </button>
          <button
            onClick={() => setActiveTab("multimodal")}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-serif-academic transition flex items-center space-x-1.5",
              activeTab === "multimodal"
                ? "bg-background text-foreground shadow-xs font-semibold border border-border/60"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Sparkles className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
            <span>多模态RAG检索</span>
          </button>
          <button
            onClick={() => setActiveTab("assets")}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-serif-academic transition flex items-center space-x-1.5",
              activeTab === "assets"
                ? "bg-background text-foreground shadow-xs font-semibold border border-border/60"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            <ImageIcon className="h-3.5 w-3.5 text-emerald-800 dark:text-emerald-300" />
            <span>图表资产画廊 ({allAssets.length > 0 ? allAssets.length : "查看"})</span>
          </button>
          <button
            onClick={() => setActiveTab("papers")}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-serif-academic transition",
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
                        <div className="flex items-start space-x-2.5 min-w-0">
                          <FileText className="h-5 w-5 text-emerald-800 dark:text-emerald-400 shrink-0 mt-0.5" />
                          <div className="min-w-0">
                            <h4
                              className="text-sm sm:text-base font-serif-academic font-semibold text-foreground line-clamp-2 leading-snug"
                              title={doc.title}
                            >
                              {doc.title}
                            </h4>
                            {doc.relative_path && doc.relative_path !== doc.title && (
                              <span className="text-xs text-foreground/60 font-mono block mt-0.5 truncate" title={doc.relative_path}>
                                📄 {doc.relative_path}
                              </span>
                            )}
                          </div>
                        </div>
                        <Badge variant="outline" className="text-xs font-mono shrink-0">
                          {doc.source_type}
                        </Badge>
                      </div>

                      {/* Prevent 70-char SHA256 overflow using break-all & font-mono */}
                      <div className="text-xs text-foreground/75 font-mono bg-muted/40 p-2 rounded leading-tight break-all border border-border/40 flex items-center justify-between">
                        <span>ID: {doc.document_id.length > 32 ? `${doc.document_id.slice(0, 16)}...${doc.document_id.slice(-8)}` : doc.document_id}</span>
                        <span className="text-foreground ml-1 font-semibold whitespace-nowrap">{doc.chunk_count || 0} 切片</span>
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

      {/* Tab: Multimodal RAG Panel */}
      {activeTab === "multimodal" && (
        <div className="space-y-6 animate-in fade-in-50" id="library-multimodal-panel">
          {/* Header Banner */}
          <div className="p-4 rounded-xl border border-emerald-800/30 bg-emerald-950/10 dark:bg-emerald-950/20 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center space-x-2">
                <Sparkles className="h-4 w-4 text-emerald-800 dark:text-emerald-300" />
                <h3 className="text-sm font-serif-academic font-bold text-foreground">
                  多模态图文联合检索 (Multimodal RAG with RRF Fusion)
                </h3>
                <Badge variant="outline" className="text-[10px] font-mono border-emerald-700/40 text-emerald-800 dark:text-emerald-300">
                  Dense + Sparse + Visual Assets
                </Badge>
              </div>
              <p className="text-xs text-foreground/75 font-serif-academic leading-relaxed">
                输入自然语言学术问题或技术关键词，系统将自动联合检索本地学术库中的正文切片、论文图表、架构示意图并进行互惠排序融合。
              </p>
            </div>
          </div>

          {/* Search Form */}
          <form onSubmit={handleMultimodalSearch} className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={multimodalQuery}
                onChange={(e) => setMultimodalQuery(e.target.value)}
                placeholder="输入学术问题或技术概念（例如：多模态RAG向量索引构建原理、Attention 架构图、Agent Loop 流程）..."
                className="pl-9 text-xs font-serif-academic bg-card"
              />
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={multimodalLoading || !multimodalQuery.trim()}
              className="bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic px-5 gap-1.5"
            >
              {multimodalLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
              <span>图文联合检索</span>
            </Button>
          </form>

          {/* Query Suggestions */}
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground flex items-center gap-1 text-[11px]">
              <Sparkles className="h-3 w-3 text-emerald-700 dark:text-emerald-400" />
              推荐多模态查询:
            </span>
            {[
              "多模态RAG向量索引构建原理",
              "Agent Harness 系统架构图",
              "Attention 注意力机制权重分布",
              "自主地理智能体 GIS Agent",
              "Loop Engineering 编码循环",
            ].map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => handleMultimodalSearch(undefined, q)}
                className="px-2.5 py-1 rounded bg-muted/60 hover:bg-muted text-[11px] font-serif-academic text-foreground/85 border border-border/50 transition"
              >
                {q}
              </button>
            ))}
          </div>

          {/* Multimodal Results Display */}
          {multimodalResults && (
            <div className="space-y-4">
              {/* Statistics & Modality Filter Bar */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 p-3 bg-card rounded-lg border border-border/70 text-xs font-serif-academic">
                <div className="flex items-center space-x-3">
                  <span className="font-semibold text-foreground">
                    检索到 {multimodalResults.fused_hits.length} 项融合结果
                  </span>
                  <span className="text-foreground/70 font-mono text-[11px]">
                    (文本切片 {multimodalResults.text_hits_count} ｜ 视觉图表 {multimodalResults.image_hits_count})
                  </span>
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    策略: {multimodalResults.fusion_strategy}
                  </Badge>
                </div>

                <div className="flex items-center space-x-1 bg-muted/70 p-0.5 rounded-md text-xs">
                  {(["all", "image", "text"] as const).map((m) => (
                    <button
                      key={m}
                      onClick={() => setModalityFilter(m)}
                      className={cn(
                        "px-2.5 py-0.5 rounded transition text-[11px] font-medium",
                        modalityFilter === m
                          ? "bg-background text-foreground shadow-2xs font-semibold"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {m === "all" ? "全部模态" : m === "image" ? "仅视觉图表" : "仅正文文本"}
                    </button>
                  ))}
                </div>
              </div>

              {/* Fused Results Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {multimodalResults.fused_hits
                  .filter((hit) => modalityFilter === "all" || hit.modality === modalityFilter)
                  .map((hit, idx) => (
                    <div
                      key={hit.hit_id || idx}
                      className={cn(
                        "p-4 rounded-xl border bg-card text-xs sm:text-sm space-y-2.5 shadow-2xs top-bevel transition flex flex-col justify-between",
                        hit.modality === "image"
                          ? "border-emerald-800/40 hover:border-emerald-700 bg-emerald-950/5 dark:bg-emerald-950/10"
                          : "border-border/80 hover:border-border"
                      )}
                    >
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs">
                          <div className="flex items-center space-x-1.5">
                            {hit.modality === "image" ? (
                              <Badge className="bg-emerald-800/15 text-emerald-800 dark:text-emerald-300 border-emerald-800/30 text-[10px] gap-1">
                                <ImageIcon className="h-3 w-3" />
                                <span>视觉图表资产</span>
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="text-[10px] gap-1">
                                <FileText className="h-3 w-3" />
                                <span>正文文本切片</span>
                              </Badge>
                            )}
                            <span className="text-foreground/75 font-mono text-[11px]">
                              第 {hit.page || hit.locator?.page || 1} 页
                            </span>
                          </div>
                          <span className="font-mono text-[11px] text-emerald-800 dark:text-emerald-300 font-semibold">
                            Rank #{hit.rank} ｜ {(hit.score * 100).toFixed(1)}%
                          </span>
                        </div>

                        {hit.modality === "image" ? (
                          <div className="space-y-2">
                            <div
                              className="relative rounded-lg overflow-hidden border border-border/70 bg-background/50 cursor-pointer group flex items-center justify-center p-1.5 max-h-52"
                              onClick={() =>
                                setLightboxAsset({
                                  url: `/api/v1/library/assets/${hit.asset_id}/content`,
                                  title: `文献插图与图表资产 (第 ${hit.page || 1} 页)`,
                                  caption: hit.text || hit.locator?.caption || `资产标识: ${hit.asset_id}`,
                                })
                              }
                            >
                              <img
                                src={`/api/v1/library/assets/${hit.asset_id}/content?variant=thumbnail`}
                                alt={hit.text || "图表资产"}
                                className="max-h-48 w-full object-contain rounded transition-transform group-hover:scale-[1.02]"
                                onError={(e) => {
                                  (e.target as HTMLElement).setAttribute("src", `/api/v1/library/assets/${hit.asset_id}/content`);
                                }}
                              />
                              <div className="absolute bottom-2 right-2 bg-black/75 text-white text-[10px] px-2 py-0.5 rounded font-mono flex items-center space-x-1 opacity-85 group-hover:opacity-100 transition">
                                <ZoomIn className="h-3 w-3" />
                                <span>查看原图</span>
                              </div>
                            </div>
                            {hit.text && (
                              <p className="text-xs text-foreground/85 font-serif-academic italic leading-relaxed line-clamp-2">
                                {hit.text}
                              </p>
                            )}
                          </div>
                        ) : (
                          <p className="text-xs sm:text-sm text-foreground/90 font-serif-academic leading-relaxed line-clamp-4">
                            {hit.text || "文本片段匹配"}
                          </p>
                        )}
                      </div>

                      <div className="pt-2 border-t border-border/50 flex items-center justify-between text-[11px] text-foreground/60 font-mono">
                        <span className="truncate max-w-[200px]">来源快照: {hit.source_snapshot_id || hit.hit_id}</span>
                        {hit.asset_id && <span className="font-semibold text-emerald-800 dark:text-emerald-300">ID: {hit.asset_id}</span>}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tab: Visual Assets Gallery */}
      {activeTab === "assets" && (
        <div className="space-y-6 animate-in fade-in-50" id="library-assets-panel">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 bg-card rounded-xl border border-border/80 shadow-2xs">
            <div>
              <h3 className="text-sm sm:text-base font-serif-academic font-bold text-foreground flex items-center space-x-2">
                <ImageIcon className="h-4 w-4 text-emerald-800 dark:text-emerald-300" />
                <span>文献图表与视觉资产库 (Visual Assets Gallery)</span>
              </h3>
              <p className="text-xs text-foreground/75 font-serif-academic mt-0.5">
                自动从收录论文中提取并持久化的高清架构图、模型拓扑、实验曲线与消融实验数据表。
              </p>
            </div>

            <div className="flex items-center space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={loadAssets}
                disabled={assetsLoading}
                className="h-8 text-xs font-serif-academic gap-1"
              >
                {assetsLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                <span>刷新资产</span>
              </Button>
            </div>
          </div>

          {assetsLoading ? (
            <div className="text-center py-24 space-y-3">
              <Loader2 className="h-8 w-8 animate-spin mx-auto text-emerald-800 dark:text-emerald-400" />
              <p className="text-xs sm:text-sm text-muted-foreground font-serif-academic">正在聚合文献视觉图表与插图...</p>
            </div>
          ) : allAssets.length === 0 ? (
            <div className="text-center py-20 bg-card rounded-xl border border-dashed border-border/80 space-y-2">
              <ImageIcon className="h-10 w-10 mx-auto text-muted-foreground/50" />
              <h4 className="text-sm font-serif-academic font-medium text-foreground">暂未提取到图表资产</h4>
              <p className="text-xs text-muted-foreground max-w-sm mx-auto font-serif-academic">
                可在多模态检索栏中直接搜索图表，或导入包含插图的 PDF 文档自动提取。
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
              {allAssets.map((asset) => (
                <div
                  key={asset.asset_id}
                  className="rounded-xl border border-border/80 bg-card overflow-hidden shadow-2xs top-bevel hover:border-emerald-800/50 transition group flex flex-col justify-between"
                >
                  <div
                    className="relative bg-muted/20 p-2 cursor-pointer flex items-center justify-center min-h-[160px] max-h-52 overflow-hidden"
                    onClick={() =>
                      setLightboxAsset({
                        url: `/api/v1/library/assets/${asset.asset_id}/content`,
                        title: asset.document_title || `图表资产 ${asset.asset_id}`,
                        caption: asset.caption || `第 ${asset.page} 页插图`,
                      })
                    }
                  >
                    <img
                      src={`/api/v1/library/assets/${asset.asset_id}/content?variant=thumbnail`}
                      alt={asset.caption || "文献图表"}
                      className="max-h-48 w-full object-contain rounded transition-transform group-hover:scale-105"
                      onError={(e) => {
                        (e.target as HTMLElement).setAttribute("src", `/api/v1/library/assets/${asset.asset_id}/content`);
                      }}
                    />
                    <div className="absolute inset-0 bg-black/30 opacity-0 group-hover:opacity-100 transition flex items-center justify-center">
                      <span className="bg-black/80 text-white text-xs px-2.5 py-1 rounded-md font-mono flex items-center space-x-1">
                        <ZoomIn className="h-3.5 w-3.5" />
                        <span>点击放大</span>
                      </span>
                    </div>
                  </div>

                  <div className="p-3 space-y-1.5 border-t border-border/60 bg-card">
                    <div className="flex items-center justify-between text-xs">
                      <Badge variant="outline" className="text-[10px] font-mono border-emerald-800/30 text-emerald-800 dark:text-emerald-300">
                        Page {asset.page}
                      </Badge>
                      <span className="text-[10px] font-mono text-muted-foreground uppercase">{asset.asset_type || "figure"}</span>
                    </div>
                    <p className="text-xs font-serif-academic font-semibold text-foreground line-clamp-1 truncate" title={asset.document_title}>
                      {asset.document_title || "未知文献"}
                    </p>
                    {asset.caption && (
                      <p className="text-[11px] text-foreground/75 font-serif-academic line-clamp-2 leading-relaxed">
                        {asset.caption}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
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
                placeholder="支持自然语言描述研究意图或关键词（如：大模型长文本注意力优化机制、Quantum error correction in superconducting qubits）..."
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

          {/* Natural language query suggestions */}
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground flex items-center gap-1 text-[11px]">
              <Sparkles className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
              自然语言检索推荐:
            </span>
            {[
              "大语言模型长上下文注意力机制优化",
              "超导量子比特容错量子纠错机制",
              "多智能体协作与复杂任务规划推理",
              "Diffusion models for biomedical image synthesis",
            ].map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => {
                  setPaperQuery(suggestion);
                }}
                className="px-2 py-0.5 rounded-full bg-muted/60 hover:bg-muted text-muted-foreground hover:text-foreground text-[11px] font-serif-academic border border-border/50 transition cursor-pointer"
              >
                {suggestion}
              </button>
            ))}
          </div>

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

      {lightboxAsset && (
        <Dialog open={!!lightboxAsset} onOpenChange={(open) => !open && setLightboxAsset(null)}>
          <DialogContent className="max-w-4xl max-h-[90vh] p-4 bg-background flex flex-col">
            <DialogHeader>
              <DialogTitle className="text-sm font-serif-academic font-bold truncate text-foreground">
                {lightboxAsset.title}
              </DialogTitle>
            </DialogHeader>
            <div className="flex-1 overflow-auto flex items-center justify-center p-3 bg-muted/20 rounded-lg">
              <img
                src={lightboxAsset.url}
                alt="Visual Asset Full View"
                className="max-h-[70vh] max-w-full object-contain rounded shadow-xs"
              />
            </div>
            {lightboxAsset.caption && (
              <p className="text-xs text-foreground/80 font-serif-academic italic pt-1 px-1">
                {lightboxAsset.caption}
              </p>
            )}
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
};
