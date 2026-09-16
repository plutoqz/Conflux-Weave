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

  const [mode, setMode] = useState<"single" | "managed" | "discovery" | "fixture">("single");
  const [query, setQuery] = useState("");
  const [topics, setTopics] = useState("");
  const [maxResults, setMaxResults] = useState(15);
  const [maxSubquestions, setMaxSubquestions] = useState(4);
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
      } else if (mode === "managed") {
        result = await api.createVerifiedResearchTask({
          objective: query.trim(),
          mode: "managed",
          max_subquestions: maxSubquestions,
        });
      } else if (mode === "single") {
        result = await api.createVerifiedResearchTask({
          objective: query.trim(),
          mode: "single",
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
            提交新的学术探索、论文检索或深度验证任务，系统将按选定模式编排执行。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          {/* Mode switch */}
          <div className="grid grid-cols-4 bg-muted/60 p-1 rounded-lg text-xs gap-1">
            <button
              type="button"
              onClick={() => setMode("single")}
              className={`py-1 px-1 rounded-md font-medium text-center transition ${
                mode === "single"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              单 Agent
            </button>
            <button
              type="button"
              onClick={() => setMode("managed")}
              className={`py-1 px-1 rounded-md font-medium text-center transition ${
                mode === "managed"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              主管协同
            </button>
            <button
              type="button"
              onClick={() => setMode("discovery")}
              className={`py-1 px-1 rounded-md font-medium text-center transition ${
                mode === "discovery"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              文献检索
            </button>
            <button
              type="button"
              onClick={() => setMode("fixture")}
              className={`py-1 px-1 rounded-md font-medium text-center transition ${
                mode === "fixture"
                  ? "bg-background text-foreground shadow-xs font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              桩基测试
            </button>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">
              {mode === "fixture" ? "验证目标" : mode === "discovery" ? "检索主题 / 问题" : "研究目标 / 核心问题"}
            </label>
            <textarea
              required
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                mode === "discovery"
                  ? "例如：DeepSeek-R1 架构分析与推理能力测评..."
                  : "例如：量子纠错码在拓扑量子计算中的最新容错阈值与实验突破..."
              }
              className="w-full text-xs p-2.5 rounded-md border border-input bg-transparent focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>

          {mode === "managed" && (
            <div className="space-y-2 p-3 rounded-lg border border-border/70 bg-muted/20">
              <div className="flex justify-between items-center">
                <label className="text-xs font-medium text-foreground">子问题拆解上限 (Subquestions)</label>
                <span className="text-xs font-mono font-semibold text-emerald-700 dark:text-emerald-300">{maxSubquestions} 个</span>
              </div>
              <input
                type="range"
                min={2}
                max={6}
                step={1}
                value={maxSubquestions}
                onChange={(e) => setMaxSubquestions(Number(e.target.value))}
                className="w-full h-1.5 bg-muted rounded-lg appearance-none cursor-pointer"
              />
              <p className="text-[11px] text-muted-foreground leading-relaxed font-serif-academic">
                主管智能体将基于目标覆盖范围，自动规划 2~{maxSubquestions} 个递进子问题并并行指派专员调研。
              </p>
            </div>
          )}

          {mode === "single" && (
            <p className="text-[11px] text-muted-foreground leading-relaxed font-serif-academic p-2 rounded bg-muted/20 border border-border/60">
              由单一深度 Agent 闭环执行多源文献检索、关键陈述提取与事实可信度交叉核验。
            </p>
          )}

          {mode === "discovery" && (
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

          {mode === "fixture" && (
            <p className="text-[11px] text-muted-foreground leading-relaxed font-serif-academic p-2 rounded bg-muted/20 border border-border/60">
              使用本地确定性离线桩基与模拟运行环境，验证流水线调度完整性与证据链一致性。
            </p>
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
