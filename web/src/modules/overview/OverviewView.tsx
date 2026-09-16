import React, { useEffect, useState } from "react";
import {
  CheckCircle2,
  AlertCircle,
  Clock,
  Sparkles,
  BookOpen,
  FolderGit2,
  Cpu,
  Layers,
  ArrowRight,
  TrendingUp,
  FileCheck,
  Zap,
  Archive,
  Trash2,
  RotateCcw,
} from "lucide-react";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { DonutChart } from "@/components/common/Charts";
import { api } from "@/services/api";
import { formatTimeAgo, cn } from "@/lib/utils";

export const OverviewView: React.FC = () => {
  const { health, setHealth, setSection, setIsNewTaskOpen, runs, setRuns, setActiveRunId } = useWorkbenchStore();
  const [docCount, setDocCount] = useState(0);
  const [runFilter, setRunFilter] = useState<"active" | "archived" | "deleted">("active");
  const [overviewStats, setOverviewStats] = useState<{
    runs: {
      total: number;
      complete: number;
      partial: number;
      working: number;
      failed: number;
      cancelled: number;
      success_rate: number;
    };
    corpus: {
      total_documents: number;
      arxiv_papers: number;
      local_pdf: number;
      local_md: number;
      structured_knowledge: number;
      visual_assets: number;
    };
    corpus_scope: string;
    provider_ok: boolean;
  } | null>(null);

  const refreshRuns = async (filter = runFilter) => {
    try {
      const res = await api.getRuns(50, filter);
      setRuns(res.items || []);
    } catch {
      // keep current list
    }
    try {
      const stats = await api.getOverviewStats();
      setOverviewStats(stats);
    } catch {
      // ignore
    }
  };
  const handleRunLifecycle = async (runId: string, action: "archive" | "delete" | "restore") => {
    try {
      await api.setRunLifecycle(runId, action);
      await refreshRuns();
    } catch (err) {
      alert(`操作失败: ${err instanceof Error ? err.message : err}`);
    }
  };

  useEffect(() => {
    refreshRuns();
    api.getHealthReady().then(setHealth).catch(() => {});
    api.getDocuments().then((res) => setDocCount(res.items?.length || 0)).catch(() => {});
    api.getOverviewStats().then(setOverviewStats).catch(() => {});

    const timer = setInterval(() => {
      refreshRuns();
    }, 4000);

    const onFocus = () => {
      refreshRuns();
    };
    window.addEventListener("focus", onFocus);

    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [setHealth, runFilter]);

  const checkList = Array.isArray(health?.checks) ? health.checks : [];
  const providerCheck = checkList.find((c) => c.name === "provider");
  const isProviderOk = overviewStats ? overviewStats.provider_ok : (providerCheck?.status === "ready");

  // Real aggregated stats directly from backend SQLite & Corpus
  const totalRuns = overviewStats ? overviewStats.runs.total : runs.length;
  const completedRuns = overviewStats
    ? overviewStats.runs.complete
    : runs.filter((r: any) => (r.state || r.status) === "complete").length;
  const partialRuns = overviewStats
    ? overviewStats.runs.partial
    : runs.filter((r: any) => (r.state || r.status) === "partial").length;
  const activeRuns = overviewStats
    ? overviewStats.runs.working
    : runs.filter((r: any) => ["working", "queued", "pending"].includes(r.state || r.status)).length;
  const failedRuns = overviewStats
    ? overviewStats.runs.failed
    : runs.filter((r: any) => (r.state || r.status) === "failed").length;

  const successRate = overviewStats
    ? overviewStats.runs.success_rate
    : totalRuns > 0
    ? Math.round(((completedRuns + partialRuns) / totalRuns) * 100)
    : 100;

  const taskSegments = [
    { label: "完整完成", value: completedRuns, color: "#1b4931" },
    { label: "部分达成", value: partialRuns, color: "#d97706" },
    { label: "进行中", value: activeRuns, color: "#0284c7" },
    { label: "失败/异常", value: failedRuns, color: "#e11d48" },
  ];

  const totalDocs = overviewStats ? overviewStats.corpus.total_documents : docCount;
  const arxivCount = overviewStats ? overviewStats.corpus.arxiv_papers : docCount;
  const localPdfCount = overviewStats ? overviewStats.corpus.local_pdf : 0;
  const localMdCount = overviewStats ? overviewStats.corpus.local_md : 0;
  const visualAssetsCount = overviewStats ? overviewStats.corpus.visual_assets : 0;

  const corpusSegments = [
    { label: "arXiv 论文", value: arxivCount, color: "#1b4931" },
    { label: "本地 PDF", value: localPdfCount, color: "#2d7a52" },
    { label: "本地 Markdown", value: localMdCount, color: "#059669" },
    { label: "视觉图表资产", value: visualAssetsCount, color: "#d97706" },
  ].filter((s) => s.value > 0);
  if (corpusSegments.length === 0) {
    corpusSegments.push({ label: "文档资料", value: totalDocs || 1, color: "#1b4931" });
  }

  return (
    <div className="max-w-6xl mx-auto p-6 sm:p-8 space-y-8 animate-in fade-in-50">
      {/* Hero Banner */}
      <div className="rounded-2xl border border-border/80 bg-card p-8 shadow-xs top-bevel relative overflow-hidden">
        <div className="max-w-3xl space-y-3 relative z-10">
          <div className="inline-flex items-center space-x-2 rounded-full border border-emerald-800/30 bg-emerald-900/10 px-3 py-1 text-xs text-emerald-800 dark:text-emerald-300 font-serif-academic font-medium">
            <Sparkles className="h-3.5 w-3.5" />
            <span>AI-Native 学术认知与代码治理工作台</span>
          </div>
          <h1 className="text-3xl font-serif-academic font-bold tracking-tight text-foreground leading-tight">
            从一个问题开始：多智能体协作与本地证据核验
          </h1>
          <p className="text-sm font-serif-academic text-muted-foreground leading-relaxed">
            面向研究者与开发者的精密认知工具箱。提供多源论文检索、深度研究协同、持久化证据链溯源、以及项目代码架构治理。
          </p>
          <div className="pt-3 flex flex-wrap items-center gap-3">
            <Button
              onClick={() => setIsNewTaskOpen(true)}
              size="sm"
              className="gap-1.5 bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic shadow-xs"
            >
              <span>新建研究任务</span>
              <ArrowRight className="h-4 w-4" />
            </Button>
            <Button
              onClick={() => setSection("chat")}
              variant="outline"
              size="sm"
              className="font-serif-academic border-border/80"
            >
              去对话提问
            </Button>
            <Button
              onClick={() => setSection("library")}
              variant="ghost"
              size="sm"
              className="font-serif-academic text-muted-foreground hover:text-foreground"
            >
              浏览文献资料库
            </Button>
          </div>
        </div>
      </div>

      {/* 4 Core KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="hover:border-border transition shadow-xs">
          <CardHeader className="p-4 pb-1">
            <div className="flex items-center justify-between">
              <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/85">累计研究任务</span>
              <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-emerald-900/10 text-emerald-800 dark:text-emerald-300 font-semibold">
                {successRate}% 成功率
              </span>
            </div>
          </CardHeader>
          <CardContent className="p-4 pt-1">
            <div className="text-2xl sm:text-3xl font-bold font-mono text-foreground">{totalRuns}</div>
            <p className="text-xs sm:text-sm font-serif-academic text-foreground/75 font-normal mt-1">已落盘持久化研究</p>
          </CardContent>
        </Card>

        <Card className="hover:border-border transition shadow-xs">
          <CardHeader className="p-4 pb-1">
            <div className="flex items-center justify-between">
              <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/85">知识库收录资产</span>
              <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-sky-900/10 text-sky-800 dark:text-sky-300 font-semibold">
                问答就绪
              </span>
            </div>
          </CardHeader>
          <CardContent className="p-4 pt-1">
            <div className="text-2xl sm:text-3xl font-bold font-mono text-foreground">{totalDocs}</div>
            <p className="text-xs sm:text-sm font-serif-academic text-foreground/75 font-normal mt-1">本地文档与论文</p>
          </CardContent>
        </Card>

        <Card className="hover:border-border transition shadow-xs">
          <CardHeader className="p-4 pb-1">
            <div className="flex items-center justify-between">
              <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/85">语料库检索范围</span>
              <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-amber-900/10 text-amber-800 dark:text-amber-300 font-semibold">
                LanceDB
              </span>
            </div>
          </CardHeader>
          <CardContent className="p-4 pt-1">
            <div className="text-base sm:text-lg font-bold font-mono text-foreground truncate">{overviewStats?.corpus_scope || health?.corpus_scope || "arxiv-oa"}</div>
            <p className="text-xs sm:text-sm font-serif-academic text-foreground/75 font-normal mt-1">混合多模态检索索引</p>
          </CardContent>
        </Card>

        <Card className="hover:border-border transition shadow-xs">
          <CardHeader className="p-4 pb-1">
            <div className="flex items-center justify-between">
              <span className="text-xs sm:text-sm font-serif-academic font-medium text-foreground/85">推理引擎状态</span>
              <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-emerald-900/10 text-emerald-800 dark:text-emerald-300 font-semibold">
                {isProviderOk ? "健康就绪" : "Mock/离线"}
              </span>
            </div>
          </CardHeader>
          <CardContent className="p-4 pt-1">
            <div className="text-base sm:text-lg font-bold font-mono text-foreground truncate">
              {isProviderOk ? "Active Provider" : "Mock Passive"}
            </div>
            <p className="text-xs sm:text-sm font-serif-academic text-foreground/75 font-normal mt-1">LLM 推理管道正常</p>
          </CardContent>
        </Card>
      </div>

      {/* 2 Data Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="shadow-xs">
          <CardHeader className="p-5 pb-2">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/70 font-semibold">Analytics</span>
                <CardTitle className="text-base sm:text-lg font-serif-academic">研究任务状态全景分布</CardTitle>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs sm:text-sm font-serif-academic text-emerald-800 dark:text-emerald-300 font-medium"
                onClick={() => setSection("research")}
              >
                进入研究
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-5 pt-0">
            <DonutChart
              segments={taskSegments}
              centerTitle="总研究数"
              centerValue={String(totalRuns)}
            />
          </CardContent>
        </Card>

        <Card className="shadow-xs">
          <CardHeader className="p-5 pb-2">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/70 font-semibold">Corpus</span>
                <CardTitle className="text-base sm:text-lg font-serif-academic">知识资产格式与构成</CardTitle>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs sm:text-sm font-serif-academic text-emerald-800 dark:text-emerald-300 font-medium"
                onClick={() => setSection("library")}
              >
                管理资料
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-5 pt-0">
            <DonutChart
              segments={corpusSegments}
              centerTitle="知识库条目"
              centerValue={String(totalDocs)}
            />
          </CardContent>
        </Card>
      </div>

      {/* Recent Runs & Systems Checks Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Runs */}
        <Card className="shadow-xs">
          <CardHeader className="p-5 pb-2">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/70 font-semibold">Recent</span>
                <CardTitle className="text-base sm:text-lg font-serif-academic">最近学术研究</CardTitle>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs sm:text-sm font-serif-academic text-foreground/80 hover:text-foreground"
                onClick={() => setSection("research")}
              >
                查看全部 ({runs.length})
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-5 pt-2 space-y-2">
            <div className="flex items-center gap-1.5 pb-1">
              {([
                ["active", "进行中"],
                ["archived", "已归档"],
                ["deleted", "回收站"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => {
                    setRunFilter(value);
                    refreshRuns(value);
                  }}
                  className={cn(
                    "text-xs px-2 py-1 rounded-md font-serif-academic transition cursor-pointer border",
                    runFilter === value
                      ? "bg-primary/10 text-primary dark:text-emerald-200 border-primary/30 font-semibold"
                      : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/60"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            {runs.length === 0 ? (
              <div className="text-center py-8 text-xs sm:text-sm font-serif-academic text-foreground/75">
                {runFilter === "active" ? "还没有研究记录，点击“新建研究任务”开启第一次学术探索。" : runFilter === "archived" ? "暂无已归档研究。" : "回收站为空。"}
              </div>
            ) : (
              runs.slice(0, 5).map((r) => (
                <div
                  key={r.run_id}
                  onClick={() => {
                    setActiveRunId(r.run_id);
                    setSection("research");
                  }}
                  className="group/run p-3.5 rounded-lg border border-border/70 hover:border-emerald-800/40 bg-muted/20 hover:bg-muted/40 transition cursor-pointer flex items-center justify-between"
                >
                  <div className="min-w-0 pr-3">
                    <h4 className="text-sm font-serif-academic font-semibold text-foreground truncate">
                      {r.title || r.query || r.run_id}
                    </h4>
                    <p className="text-xs font-mono text-foreground/70 mt-1">
                      {formatTimeAgo(r.updated_at || r.created_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span
                      className={cn(
                        "px-2.5 py-0.5 rounded text-xs font-mono uppercase font-medium",
                        r.status === "complete" ? "bg-emerald-900/10 text-emerald-800 dark:text-emerald-300" : "bg-muted text-foreground/80"
                      )}
                    >
                      {r.status}
                    </span>
                    <span className="hidden group-hover/run:flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                      {runFilter === "active" ? (
                        <>
                          <button type="button" title="归档" className="text-muted-foreground hover:text-foreground cursor-pointer bg-transparent border-none p-0" onClick={() => handleRunLifecycle(r.run_id, "archive")}>
                            <Archive className="h-3.5 w-3.5" />
                          </button>
                          <button type="button" title="移入回收站" className="text-muted-foreground hover:text-red-600 cursor-pointer bg-transparent border-none p-0" onClick={() => handleRunLifecycle(r.run_id, "delete")}>
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </>
                      ) : (
                        <button type="button" title="恢复" className="text-muted-foreground hover:text-emerald-700 cursor-pointer bg-transparent border-none p-0" onClick={() => handleRunLifecycle(r.run_id, "restore")}>
                          <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </span>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* System Diagnostics Checklist */}
        <Card className="shadow-xs">
          <CardHeader className="p-5 pb-2">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-xs font-mono uppercase tracking-wider text-foreground/70 font-semibold">Systems</span>
                <CardTitle className="text-base sm:text-lg font-serif-academic">系统就绪诊断检查</CardTitle>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs sm:text-sm font-serif-academic text-foreground/80 hover:text-foreground"
                onClick={() => setSection("settings")}
              >
                配置
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-5 pt-2 space-y-2.5">
            {checkList.map((c, i) => (
              <div
                key={i}
                className="p-3.5 rounded-lg border border-border/70 bg-muted/20 flex items-center justify-between text-xs sm:text-sm"
              >
                <div className="flex items-center space-x-2.5">
                  <CheckCircle2 className="h-4 w-4 text-emerald-800 dark:text-emerald-400 shrink-0" />
                  <div>
                    <span className="font-semibold font-mono text-foreground">{c.name}</span>
                    <p className="text-foreground/75 text-xs sm:text-sm font-serif-academic mt-0.5">
                      {c.message || "正常运行"}
                    </p>
                  </div>
                </div>
                <span className="font-mono text-xs text-emerald-800 dark:text-emerald-300 uppercase font-semibold">
                  {c.status}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};
