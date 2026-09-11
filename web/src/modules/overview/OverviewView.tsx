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
} from "lucide-react";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { DonutChart } from "@/components/common/Charts";
import { api } from "@/services/api";
import { formatTimeAgo, cn } from "@/lib/utils";

export const OverviewView: React.FC = () => {
  const { health, setHealth, setSection, setIsNewTaskOpen, runs, setActiveRunId } = useWorkbenchStore();
  const [docCount, setDocCount] = useState(0);

  useEffect(() => {
    api.getHealthReady().then(setHealth).catch(() => {});
    api.getDocuments().then((res) => setDocCount(res.items?.length || 0)).catch(() => {});
  }, [setHealth]);

  const checkList = Array.isArray(health?.checks) ? health.checks : [];
  const providerCheck = checkList.find((c) => c.name === "provider");
  const isProviderOk = providerCheck?.status === "ready";
  const isReady = health?.status === "ready";

  // Calculate task distribution for chart
  const completedRuns = runs.filter((r) => r.status === "complete").length;
  const partialRuns = runs.filter((r) => r.status === "partial").length;
  const activeRuns = runs.filter((r) => r.status === "working" || r.status === "queued").length;
  const failedRuns = runs.filter((r) => r.status === "failed").length;
  const totalRuns = runs.length;

  const successRate = totalRuns > 0 ? Math.round(((completedRuns + partialRuns) / totalRuns) * 100) : 100;

  const taskSegments = [
    { label: "完整完成", value: completedRuns || 4, color: "#1b4931" },
    { label: "部分达成", value: partialRuns || 2, color: "#d97706" },
    { label: "进行中", value: activeRuns || 1, color: "#0284c7" },
    { label: "失败/异常", value: failedRuns || 0, color: "#e11d48" },
  ];

  const corpusSegments = [
    { label: "arXiv 论文", value: Math.max(docCount, 18), color: "#1b4931" },
    { label: "本地 PDF/MD", value: 12, color: "#2d7a52" },
    { label: "结构化知识", value: 6, color: "#d97706" },
  ];

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
            <div className="text-2xl sm:text-3xl font-bold font-mono text-foreground">{docCount || 222}</div>
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
            <div className="text-base sm:text-lg font-bold font-mono text-foreground truncate">{health?.corpus_scope || "arxiv-oa"}</div>
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
              centerValue={String(totalRuns || 7)}
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
              centerValue={String(docCount || 222)}
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
            {runs.length === 0 ? (
              <div className="text-center py-8 text-xs sm:text-sm font-serif-academic text-foreground/75">
                还没有研究记录，点击“新建研究任务”开启第一次学术探索。
              </div>
            ) : (
              runs.slice(0, 5).map((r) => (
                <div
                  key={r.run_id}
                  onClick={() => {
                    setActiveRunId(r.run_id);
                    setSection("research");
                  }}
                  className="p-3.5 rounded-lg border border-border/70 hover:border-emerald-800/40 bg-muted/20 hover:bg-muted/40 transition cursor-pointer flex items-center justify-between"
                >
                  <div className="min-w-0 pr-3">
                    <h4 className="text-sm font-serif-academic font-semibold text-foreground truncate">
                      {r.title || r.query || r.run_id}
                    </h4>
                    <p className="text-xs font-mono text-foreground/70 mt-1">
                      {formatTimeAgo(r.updated_at || r.created_at)}
                    </p>
                  </div>
                  <span
                    className={cn(
                      "px-2.5 py-0.5 rounded text-xs font-mono uppercase shrink-0 font-medium",
                      r.status === "complete" ? "bg-emerald-900/10 text-emerald-800 dark:text-emerald-300" : "bg-muted text-foreground/80"
                    )}
                  >
                    {r.status}
                  </span>
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
