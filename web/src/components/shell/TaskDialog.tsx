import React, { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { api } from "@/services/api";

export const TaskDialog: React.FC = () => {
  const { isNewTaskOpen, setIsNewTaskOpen, setRuns, setActiveRunId, setSection } =
    useWorkbenchStore();

  const [mode, setMode] = useState<"single" | "managed" | "fixture">("single");
  const [query, setQuery] = useState("");
  const [topics, setTopics] = useState("");
  const [maxResults, setMaxResults] = useState(15);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setSubmitting(true);
    setError(null);
    try {
      let result;
      if (mode === "fixture") {
        result = await api.createFixtureTask({
          objective: query.trim(),
        });
      } else {
        const topicList = topics
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean);
        result = await api.createResearchTask({
          query: query.trim(),
          topics: topicList.length > 0 ? topicList : undefined,
          max_results: maxResults,
        });
      }

      setIsNewTaskOpen(false);
      setQuery("");
      setTopics("");

      // Refresh runs list
      const updated = await api.getRuns();
      setRuns(updated.items || []);
      if (result.run_id) {
        setActiveRunId(result.run_id);
        setSection("research");
      }
    } catch (err: any) {
      setError(err.message || "创建研究任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={isNewTaskOpen} onOpenChange={setIsNewTaskOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>新建研究任务</DialogTitle>
          <DialogDescription>
            提交新的学术探索或论文检索任务，系统将自动化执行检索、排序与证据归纳。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          {/* Mode switch */}
          <div className="flex bg-muted/60 p-1 rounded-lg text-xs">
            <button
              type="button"
              onClick={() => setMode("single")}
              className={`flex-1 py-1 rounded-md font-medium transition ${
                mode === "single"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              单 Agent 探索
            </button>
            <button
              type="button"
              onClick={() => setMode("managed")}
              className={`flex-1 py-1 rounded-md font-medium transition ${
                mode === "managed"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              主管协同管理
            </button>
            <button
              type="button"
              onClick={() => setMode("fixture")}
              className={`flex-1 py-1 rounded-md font-medium transition ${
                mode === "fixture"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              离线桩基测试
            </button>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">研究目标 / 核心问题</label>
            <textarea
              required
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="例如：量子纠错码在拓扑量子计算中的最新容错阈值与实验突破..."
              className="w-full text-xs p-2.5 rounded-md border border-input bg-transparent focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>

          {mode !== "fixture" && (
            <>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">关键词 / 领域焦点 (可选，英文逗号分隔)</label>
                <Input
                  value={topics}
                  onChange={(e) => setTopics(e.target.value)}
                  placeholder="Surface codes, fault tolerance, Majorana"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">最大候选论文数量 ({maxResults})</label>
                <input
                  type="range"
                  min={5}
                  max={50}
                  step={5}
                  value={maxResults}
                  onChange={(e) => setMaxResults(Number(e.target.value))}
                  className="w-full h-1.5 bg-muted rounded-lg appearance-none cursor-pointer"
                />
              </div>
            </>
          )}

          {error && <div className="text-xs text-rose-500 bg-rose-500/10 p-2 rounded-md">{error}</div>}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setIsNewTaskOpen(false)}
            >
              取消
            </Button>
            <Button type="submit" size="sm" disabled={submitting}>
              {submitting ? "提交中..." : "启动研究"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};
