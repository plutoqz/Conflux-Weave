import React, { useState, useEffect } from "react";
import {
  Plus,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  ChevronDown,
  Clock,
  BookOpen,
  Layers,
  Database,
  Cpu,
} from "lucide-react";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { Button } from "@/components/ui/button";
import { formatTimeAgo, cn } from "@/lib/utils";
import type { RunSummary, RunStatus } from "@/types/workbench";

export const Sidebar: React.FC = () => {
  const {
    zenMode,
    toggleZenMode,
    hudOpen,
    toggleHud,
    runs,
    activeRunId,
    setActiveRunId,
    setSection,
    health,
    setIsNewTaskOpen,
  } = useWorkbenchStore();

  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "completed" | "active">("all");

  // Auto-select first run if none selected
  useEffect(() => {
    if (!activeRunId && runs.length > 0) {
      setActiveRunId(runs[0].run_id);
    }
  }, [activeRunId, runs, setActiveRunId]);

  // Keyboard shortcut '[' to toggle zen mode
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "[") {
        e.preventDefault();
        toggleZenMode();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [toggleZenMode]);

  const filteredRuns = runs.filter((run) => {
    if (filter === "completed" && run.status !== "complete" && run.status !== "partial") return false;
    if (filter === "active" && (run.status === "complete" || run.status === "failed" || run.status === "cancelled")) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      const matchTitle = (run.title || run.query || run.run_id).toLowerCase().includes(q);
      return matchTitle;
    }
    return true;
  });

  const getStatusColor = (status: RunStatus) => {
    switch (status) {
      case "complete":
        return "bg-emerald-500 ring-emerald-500/20";
      case "partial":
        return "bg-amber-500 ring-amber-500/20";
      case "failed":
        return "bg-rose-500 ring-rose-500/20";
      case "working":
      case "queued":
        return "bg-sky-500 ring-sky-500/20 animate-pulse";
      default:
        return "bg-muted-foreground ring-muted/20";
    }
  };

  // Mini rail mode (Zen Focus)
  if (zenMode) {
    return (
      <aside className="w-12 shrink-0 border-r border-border/70 bg-card/40 flex flex-col items-center py-3 space-y-3">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggleZenMode}
          title="展开侧栏 ([)"
        >
          <PanelLeftOpen className="h-4 w-4 text-muted-foreground" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setIsNewTaskOpen(true)}
          title="新建研究"
        >
          <Plus className="h-4 w-4 text-emerald-500" />
        </Button>
        <div className="w-6 h-[1px] bg-border my-1" />
        <div className="flex-1 w-full overflow-y-auto flex flex-col items-center space-y-2 py-1">
          {runs.slice(0, 15).map((run) => (
            <button
              key={run.run_id}
              onClick={() => {
                setActiveRunId(run.run_id);
                setSection("research");
              }}
              title={run.title || run.query || run.run_id}
              className={cn(
                "h-7 w-7 rounded-full flex items-center justify-center transition hover:scale-110",
                activeRunId === run.run_id ? "bg-accent border border-emerald-500" : ""
              )}
            >
              <span className={cn("h-2.5 w-2.5 rounded-full ring-2", getStatusColor(run.status))} />
            </button>
          ))}
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-64 shrink-0 border-r border-border/70 bg-card/40 flex flex-col h-[calc(100vh-3.5rem)]">
      {/* Sidebar Heading */}
      <div className="p-3 border-b border-border/60 flex items-center justify-between">
        <div>
          <span className="text-xs font-mono tracking-wider uppercase text-foreground/75 font-semibold block">
            History
          </span>
          <h2 className="text-sm font-bold text-foreground leading-tight">最近研究</h2>
        </div>
        <div className="flex items-center space-x-1">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setIsNewTaskOpen(true)}
            title="新建研究"
          >
            <Plus className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={toggleZenMode}
            title="折叠侧栏 ([)"
          >
            <PanelLeftClose className="h-4 w-4 text-muted-foreground" />
          </Button>
        </div>
      </div>

      {/* Search & Pills */}
      <div className="p-3 border-b border-border/60 space-y-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索研究历史..."
            className="w-full pl-8 pr-2.5 py-1 text-xs rounded-md bg-muted/50 border border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <div className="flex items-center space-x-1 bg-muted/40 p-0.5 rounded-lg text-xs">
          {(["all", "completed", "active"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={cn(
                "flex-1 py-1 rounded-md font-medium transition text-center capitalize",
                filter === t
                  ? "bg-background text-foreground shadow-xs"
                  : "text-foreground/70 hover:text-foreground"
              )}
            >
              {t === "all" ? "全部" : t === "completed" ? "已完成" : "进行中"}
            </button>
          ))}
        </div>
      </div>

      {/* Systems HUD Toggle */}
      <div className="border-b border-border/60">
        <button
          onClick={toggleHud}
          className="w-full px-3 py-2 flex items-center justify-between text-xs text-foreground/80 hover:bg-muted/40 transition"
        >
          <span className="font-mono text-xs tracking-wider uppercase font-semibold">Systems HUD</span>
          <ChevronDown
            className={cn("h-3.5 w-3.5 transition-transform", hudOpen ? "rotate-180" : "")}
          />
        </button>
        {hudOpen && (
          <div className="px-3 pb-3 pt-1 bg-muted/20 text-xs space-y-1.5 border-t border-border/40">
            <div className="flex justify-between items-center text-xs">
              <span className="text-foreground/70">Corpus</span>
              <span className="font-mono text-foreground font-medium">{health?.corpus_scope || "arxiv-oa"}</span>
            </div>
            <div className="flex justify-between items-center text-xs">
              <span className="text-foreground/70">Provider</span>
              <span className="font-mono text-foreground font-medium">{health?.provider_ready ? "Configured" : "Mock/Passive"}</span>
            </div>
          </div>
        )}
      </div>

      {/* Task List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {filteredRuns.length === 0 ? (
          <div className="text-center py-8 px-4 text-xs text-foreground/70">
            {search ? "没有找到符合条件的历史" : "暂无历史学术研究"}
          </div>
        ) : (
          filteredRuns.map((run) => {
            const isActive = run.run_id === activeRunId;
            return (
              <div
                key={run.run_id}
                onClick={() => {
                  setActiveRunId(run.run_id);
                  setSection("research");
                }}
                className={cn(
                  "p-2.5 rounded-lg border text-left cursor-pointer transition relative group",
                  isActive
                    ? "bg-card border-emerald-800/60 shadow-xs"
                    : "border-border/50 hover:bg-muted/40 hover:border-border"
                )}
              >
                {isActive && (
                  <span className="absolute left-0 top-2 bottom-2 w-[2.5px] bg-emerald-800 dark:bg-emerald-400 rounded-r-full" />
                )}
                <div className="flex items-start justify-between space-x-2">
                  <div className="flex items-center space-x-2 min-w-0">
                    <span
                      className={cn("h-2 w-2 rounded-full shrink-0 ring-2", getStatusColor(run.status))}
                    />
                    <h4 className="text-sm font-serif-academic font-semibold text-foreground truncate">
                      {run.title || run.query || run.run_id}
                    </h4>
                  </div>
                </div>
                <div className="flex items-center space-x-2 mt-1.5 text-xs text-foreground/75 font-mono">
                  <span>{formatTimeAgo(run.updated_at || run.created_at)}</span>
                  {run.evidence_count !== undefined && run.evidence_count > 0 && (
                    <>
                      <span>·</span>
                      <span className="text-emerald-700 dark:text-emerald-400 font-semibold">
                        {run.evidence_count} 证据
                      </span>
                    </>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
};
