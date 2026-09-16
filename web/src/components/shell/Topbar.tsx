import React, { useState, useMemo } from "react";
import {
  Sun,
  Moon,
  Sparkles,
  Activity,
  Layers,
  Loader2,
  CheckCircle2,
  XCircle,
  ExternalLink,
  Trash2,
} from "lucide-react";
import { useWorkbenchStore, type GlobalBackgroundTask } from "@/stores/useWorkbenchStore";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { GlobalSearch } from "@/components/shell/GlobalSearch";
import type { SectionType } from "@/types/workbench";

const navItems: Array<{ id: SectionType; label: string }> = [
  { id: "overview", label: "总览" },
  { id: "chat", label: "对话" },
  { id: "research", label: "研究" },
  { id: "library", label: "资料库" },
  { id: "projects", label: "项目" },
  { id: "skills", label: "技能" },
  { id: "settings", label: "设置" },
];

export const Topbar: React.FC = () => {
  const {
    section,
    setSection,
    theme,
    toggleTheme,
    health,
    runs,
    backgroundTasks,
    removeBackgroundTask,
    openNoteStudio,
    setActiveRunId,
  } = useWorkbenchStore();

  const [isTaskCenterOpen, setIsTaskCenterOpen] = useState(false);

  const activeRunsAsTasks: GlobalBackgroundTask[] = useMemo(() => {
    return (runs || [])
      .filter((r) => r.state === "working" || r.state === "accepted" || r.state === "queued" || r.state === "needs_attention")
      .filter((r) => !backgroundTasks.some((bt) => bt.id === r.run_id))
      .map((r) => ({
        id: r.run_id,
        title: r.query || r.run_id,
        type: "research" as const,
        status: r.state === "needs_attention" ? ("failed" as const) : ("running" as const),
        startTime: r.created_at ? new Date(r.created_at).getTime() : Date.now(),
        message: r.state === "needs_attention" ? "需要人工干预恢复" : "后台研究任务持续运行中...",
        targetId: r.run_id,
      }));
  }, [runs, backgroundTasks]);

  const allTasks = useMemo(() => [...backgroundTasks, ...activeRunsAsTasks], [backgroundTasks, activeRunsAsTasks]);

  const isReady = health?.status === "ready";
  const runningTasks = allTasks.filter((t) => t.status === "running");
  const runningCount = runningTasks.length;

  const handleOpenTask = (task: GlobalBackgroundTask) => {
    setIsTaskCenterOpen(false);
    if (task.type === "reading" && task.targetId) {
      openNoteStudio(task.targetId);
    } else if (task.type === "research" && task.targetId) {
      setActiveRunId(task.targetId);
      setSection("research");
    } else if (task.type === "project") {
      setSection("projects");
    }
  };

  return (
    <header className="sticky top-0 z-40 flex h-14 w-full items-center justify-between border-b border-border/70 bg-background/80 px-4 backdrop-blur-md">
      {/* Brand & Nav */}
      <div className="flex items-center space-x-3 sm:space-x-6 min-w-0">
        <button
          onClick={() => setSection("overview")}
          className="flex items-center space-x-2.5 text-left transition hover:opacity-90 cursor-pointer"
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-900 text-white font-serif-academic font-bold text-xs shadow-xs top-bevel">
            CW
          </div>
          <div className="hidden sm:flex flex-col">
            <span className="text-sm font-serif-academic font-semibold tracking-tight leading-none text-foreground">
              Conflux Weave
            </span>
            <span className="text-xs text-foreground/75 font-mono leading-none mt-1 font-medium">
              Research Workbench
            </span>
          </div>
        </button>

        <nav
          className="flex items-center space-x-0.5 sm:space-x-1 overflow-x-auto max-w-[62vw] sm:max-w-none [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          aria-label="工作台分区"
        >
          {navItems.map((item) => {
            const active = section === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setSection(item.id)}
                className={`relative px-2 sm:px-3.5 py-1.5 text-xs sm:text-sm font-serif-academic transition-all rounded-md shrink-0 whitespace-nowrap cursor-pointer ${
                  active
                    ? "bg-card text-foreground font-semibold shadow-2xs border border-border/70"
                    : "text-foreground/75 hover:text-foreground hover:bg-muted/50"
                }`}
              >
                {item.label}
                {active && (
                  <span className="absolute bottom-0 left-2 right-2 h-[2px] bg-emerald-800 dark:bg-emerald-400 rounded-full" />
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Right Actions */}
      <div className="flex items-center space-x-2 sm:space-x-3">
        {/* A1 全局搜索 */}
        <GlobalSearch />

        {/* Global Task Center (异步并发任务中心) */}
        <div className="relative">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIsTaskCenterOpen((prev) => !prev)}
            className={`h-8 px-2.5 gap-1.5 text-xs font-mono border-border/80 ${
              runningCount > 0 ? "border-emerald-600/50 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300" : ""
            }`}
            title="查看并发与后台运行任务"
          >
            {runningCount > 0 ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-600" />
            ) : (
              <Layers className="h-3.5 w-3.5" />
            )}
            <span className="hidden md:inline">任务中心</span>
            {allTasks.length > 0 && (
              <Badge
                variant={runningCount > 0 ? "default" : "secondary"}
                className={`text-[10px] px-1 py-0 h-4 font-mono ${
                  runningCount > 0 ? "bg-emerald-700 text-white" : ""
                }`}
              >
                {runningCount > 0 ? `${runningCount} 运行中` : allTasks.length}
              </Badge>
            )}
          </Button>

          {/* Task Center Dropdown Popover */}
          {isTaskCenterOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setIsTaskCenterOpen(false)}
              />
              <div className="absolute right-0 top-10 z-50 w-80 sm:w-96 rounded-xl border border-border bg-card p-3 shadow-lg top-bevel space-y-2">
                <div className="flex items-center justify-between pb-2 border-b border-border/60">
                  <div className="flex items-center gap-1.5">
                    <Layers className="h-4 w-4 text-emerald-800 dark:text-emerald-300" />
                    <span className="text-xs font-serif-academic font-bold text-foreground">
                      并发与后台任务中心 ({allTasks.length})
                    </span>
                  </div>
                  {allTasks.some((t) => t.status !== "running") && (
                    <button
                      onClick={() => {
                        backgroundTasks
                          .filter((t) => t.status !== "running")
                          .forEach((t) => removeBackgroundTask(t.id));
                      }}
                      className="text-[11px] font-serif-academic text-muted-foreground hover:text-foreground cursor-pointer flex items-center gap-1"
                    >
                      <Trash2 className="h-3 w-3" />
                      清除完成
                    </button>
                  )}
                </div>

                {allTasks.length === 0 ? (
                  <div className="py-8 text-center text-xs text-muted-foreground font-serif-academic">
                    当前没有正在执行或待处理的后台任务。
                  </div>
                ) : (
                  <div className="max-h-72 overflow-y-auto space-y-1.5 pr-0.5">
                    {allTasks.map((t) => {
                      const elapsed = Math.round((Date.now() - t.startTime) / 1000);
                      return (
                        <div
                          key={t.id}
                          className="p-2.5 rounded-lg border border-border/70 bg-background text-xs space-y-1 hover:border-border transition"
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-1.5 font-medium text-foreground truncate max-w-[200px]">
                              {t.status === "running" ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-600 shrink-0" />
                              ) : t.status === "succeeded" ? (
                                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                              ) : (
                                <XCircle className="h-3.5 w-3.5 text-rose-600 shrink-0" />
                              )}
                              <span className="truncate">{t.title}</span>
                            </div>
                            <span className="text-[10px] font-mono text-muted-foreground">
                              {elapsed}s 前
                            </span>
                          </div>

                          <div className="flex items-center justify-between pt-1">
                            <span className="text-[11px] text-muted-foreground">
                              {t.message || (t.status === "running" ? "正在后台异步执行中..." : "任务已完成")}
                            </span>
                            <div className="flex items-center gap-1">
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => handleOpenTask(t)}
                                className="h-6 px-2 text-[11px] text-emerald-800 dark:text-emerald-300 hover:bg-emerald-500/10 gap-1 font-serif-academic"
                              >
                                <ExternalLink className="h-3 w-3" />
                                <span>打开</span>
                              </Button>
                              {t.status !== "running" && (
                                <button
                                  onClick={() => removeBackgroundTask(t.id)}
                                  className="text-muted-foreground hover:text-foreground p-1 cursor-pointer"
                                  title="移除记录"
                                >
                                  <Trash2 className="h-3 w-3" />
                                </button>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Health Dot */}
        <div
          className="flex items-center space-x-2 rounded-full border border-border/80 bg-muted/40 px-3 py-1 text-xs text-foreground/80 font-medium"
          title={isReady ? "系统就绪" : "系统正在检查或降级"}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              isReady ? "bg-emerald-500 animate-pulse" : "bg-amber-500"
            }`}
          />
          <span className="font-mono text-xs font-semibold">
            {isReady ? "READY" : "CHECKING"}
          </span>
        </div>

        {/* Theme Toggle */}
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggleTheme}
          title={theme === "dark" ? "切换至浅色模式" : "切换至深色模式"}
          aria-label="切换深浅主题"
        >
          {theme === "dark" ? (
            <Sun className="h-4 w-4 text-amber-400" />
          ) : (
            <Moon className="h-4 w-4 text-muted-foreground" />
          )}
        </Button>
      </div>
    </header>
  );
};
