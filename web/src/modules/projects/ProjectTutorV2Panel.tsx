import React, { useState, useEffect } from "react";
import {
  ShieldAlert,
  ShieldCheck,
  Activity,
  Layers,
  Network,
  GitBranch,
  BookOpen,
  Code2,
  RefreshCw,
  Sparkles,
  ExternalLink,
  ChevronRight,
  AlertTriangle,
  CheckCircle2,
  FileCode,
  SlidersHorizontal,
  Wand2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import { renderMarkdownWithMath } from "@/lib/math";
import type {
  ProjectTutorDissectResponse,
  ProjectHygieneAuditResponse,
} from "@/types/workbench";

interface ProjectTutorV2PanelProps {
  projectId: string;
  projectName?: string;
  projectPath: string;
  onSelectFile?: (filePath: string) => void;
  onApplyPatch?: (patchDiff: string) => void;
}

export const ProjectTutorV2Panel: React.FC<ProjectTutorV2PanelProps> = ({
  projectId,
  projectName = "项目",
  projectPath,
  onSelectFile,
  onApplyPatch,
}) => {
  const [loading, setLoading] = useState(false);
  const [dissectData, setDissectData] = useState<ProjectTutorDissectResponse | null>(null);
  const [hygieneData, setHygieneData] = useState<ProjectHygieneAuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [activeSubTab, setActiveSubTab] = useState<
    "hygiene" | "decisions" | "dependencies" | "topology" | "walkthrough"
  >("hygiene");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  const loadData = async () => {
    if (!projectPath) return;
    setLoading(true);
    setError(null);
    try {
      const [dissectRes, hygieneRes] = await Promise.all([
        api.dissectProjectTutor({
          project_path: projectPath,
          project_name: projectName,
        }),
        api.auditProjectHygiene({
          project_path: projectPath,
          max_files: 50,
        }),
      ]);
      setDissectData(dissectRes);
      setHygieneData(hygieneRes);
    } catch (err: any) {
      setError(err.message || "加载架构导师与体检数据失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [projectPath, projectId]);

  const filteredFindings = React.useMemo(() => {
    if (!hygieneData?.findings) return [];
    if (categoryFilter === "all") return hygieneData.findings;
    return hygieneData.findings.filter((f) => f.category === categoryFilter);
  }, [hygieneData, categoryFilter]);

  const categoryLabels: Record<string, string> = {
    all: "全部事项",
    stub_impl: "未实现桩基 (pass/.../NotImplemented)",
    unhandled_exceptions: "未处理异常与空捕获",
    loose_typing: "弱类型与 Any 泛滥",
    missing_tests: "测试覆盖缺失",
    mutable_defaults: "可变默认参数",
    bare_resource_leaks: "裸 open 资源泄漏",
    async_global_mutation: "异步全局状态竞态",
  };

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      {/* Top Banner / Actions Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 rounded-xl border border-border/80 bg-card top-bevel">
        <div className="flex items-center space-x-3">
          <div className="p-2 rounded-lg bg-emerald-800/10 text-emerald-800 dark:text-emerald-400">
            <ShieldCheck className="h-6 w-6" />
          </div>
          <div>
            <h3 className="font-serif-academic font-bold text-base text-foreground">
              架构导师与 7 维 AI 体检 (Project Tutor V2)
            </h3>
            <p className="text-xs text-muted-foreground font-mono">
              工程路径: {projectPath}
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <Button
            size="sm"
            variant="outline"
            onClick={loadData}
            disabled={loading}
            className="text-xs font-serif-academic h-8 gap-1.5"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            <span>{loading ? "正在深度扫描..." : "重新体检与逆向"}</span>
          </Button>
        </div>
      </div>

      {error && (
        <div className="text-xs text-rose-600 bg-rose-500/10 p-3 rounded-lg border border-rose-500/20">
          {error}
        </div>
      )}

      {/* Metric Cards Row */}
      {hygieneData && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Score Card */}
          <div className="p-4 rounded-xl border border-border/80 bg-background space-y-1 shadow-2xs">
            <span className="text-xs font-medium text-muted-foreground">工程健康综合评分</span>
            <div className="flex items-baseline space-x-2">
              <span
                className={cn(
                  "text-2xl font-bold font-mono",
                  hygieneData.hygiene_score >= 85
                    ? "text-emerald-700 dark:text-emerald-400"
                    : hygieneData.hygiene_score >= 70
                    ? "text-amber-600 dark:text-amber-400"
                    : "text-rose-600 dark:text-rose-400"
                )}
              >
                {hygieneData.hygiene_score}
              </span>
              <span className="text-xs text-muted-foreground font-mono">/ 100</span>
            </div>
            <p className="text-[11px] text-muted-foreground truncate" title={hygieneData.executive_verdict}>
              {hygieneData.executive_verdict}
            </p>
          </div>

          {/* Findings Count Card */}
          <div className="p-4 rounded-xl border border-border/80 bg-background space-y-1 shadow-2xs">
            <span className="text-xs font-medium text-muted-foreground">检测到的问题与恶习</span>
            <div className="text-2xl font-bold font-mono text-foreground">
              {hygieneData.total_findings_count}
              <span className="text-xs font-normal text-muted-foreground ml-1">处</span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              覆盖占位伪造、资源泄漏与异步风险
            </p>
          </div>

          {/* Decisions Count Card */}
          <div className="p-4 rounded-xl border border-border/80 bg-background space-y-1 shadow-2xs">
            <span className="text-xs font-medium text-muted-foreground">核心架构决策</span>
            <div className="text-2xl font-bold font-mono text-foreground">
              {dissectData?.decisions?.length || 0}
              <span className="text-xs font-normal text-muted-foreground ml-1">组</span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              技术选型理由、备选方案与取舍
            </p>
          </div>

          {/* Dependencies Count Card */}
          <div className="p-4 rounded-xl border border-border/80 bg-background space-y-1 shadow-2xs">
            <span className="text-xs font-medium text-muted-foreground">依赖机理知识库</span>
            <div className="text-2xl font-bold font-mono text-foreground">
              {dissectData?.dependency_cards?.length || 0}
              <span className="text-xs font-normal text-muted-foreground ml-1">个模块</span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              底层运行机理与在本项目中的角色
            </p>
          </div>
        </div>
      )}

      {/* SubTab Navigation */}
      <div className="flex flex-wrap items-center gap-1.5 bg-muted/60 p-1 rounded-lg text-xs font-serif-academic border border-border/50">
        <button
          type="button"
          onClick={() => setActiveSubTab("hygiene")}
          className={cn(
            "px-3 py-1.5 rounded-md font-medium transition cursor-pointer flex items-center gap-1.5",
            activeSubTab === "hygiene"
              ? "bg-background text-foreground shadow-xs font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <ShieldAlert className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" />
          <span>7 大维度 AI 体检报告</span>
          {hygieneData && (
            <Badge variant="secondary" className="font-mono text-[10px] ml-1">
              {hygieneData.total_findings_count}
            </Badge>
          )}
        </button>

        <button
          type="button"
          onClick={() => setActiveSubTab("decisions")}
          className={cn(
            "px-3 py-1.5 rounded-md font-medium transition cursor-pointer flex items-center gap-1.5",
            activeSubTab === "decisions"
              ? "bg-background text-foreground shadow-xs font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <SlidersHorizontal className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
          <span>心智模型与技术决策</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveSubTab("dependencies")}
          className={cn(
            "px-3 py-1.5 rounded-md font-medium transition cursor-pointer flex items-center gap-1.5",
            activeSubTab === "dependencies"
              ? "bg-background text-foreground shadow-xs font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <Layers className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
          <span>依赖组件底层机理</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveSubTab("topology")}
          className={cn(
            "px-3 py-1.5 rounded-md font-medium transition cursor-pointer flex items-center gap-1.5",
            activeSubTab === "topology"
              ? "bg-background text-foreground shadow-xs font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <Network className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
          <span>架构拓扑与数据流时序</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveSubTab("walkthrough")}
          className={cn(
            "px-3 py-1.5 rounded-md font-medium transition cursor-pointer flex items-center gap-1.5",
            activeSubTab === "walkthrough"
              ? "bg-background text-foreground shadow-xs font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <BookOpen className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
          <span>源码走查 4 阶递进路径</span>
        </button>
      </div>

      {/* SUBTAB 1: HYGIENE AUDIT FINDINGS */}
      {activeSubTab === "hygiene" && hygieneData && (
        <div className="space-y-4">
          {/* Category Filter Pills */}
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            <span className="text-xs font-medium text-muted-foreground mr-1">分类筛选：</span>
            {Object.entries(categoryLabels).map(([catKey, label]) => {
              const count =
                catKey === "all"
                  ? hygieneData.total_findings_count
                  : hygieneData.findings_by_category?.[catKey] || 0;
              return (
                <button
                  key={catKey}
                  type="button"
                  onClick={() => setCategoryFilter(catKey)}
                  className={cn(
                    "text-xs px-2.5 py-1 rounded-md font-serif-academic transition cursor-pointer border flex items-center gap-1",
                    categoryFilter === catKey
                      ? "bg-primary text-white border-primary font-semibold"
                      : "bg-background border-border/70 text-muted-foreground hover:text-foreground"
                  )}
                >
                  <span>{catKey === "all" ? "全部" : catKey}</span>
                  <span className="font-mono text-[10px] opacity-80">({count})</span>
                </button>
              );
            })}
          </div>

          {/* Findings List */}
          <div className="space-y-3">
            {filteredFindings.length > 0 ? (
              filteredFindings.map((finding) => (
                <div
                  key={finding.finding_id}
                  className="p-4 rounded-xl border border-border/80 bg-card space-y-3 shadow-2xs top-bevel"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center space-x-2">
                      <Badge
                        variant={
                          finding.severity === "high"
                            ? "destructive"
                            : finding.severity === "medium"
                            ? "outline"
                            : "secondary"
                        }
                        className="font-mono text-xs uppercase font-bold"
                      >
                        {finding.severity}
                      </Badge>
                      <Badge variant="outline" className="font-mono text-xs">
                        {finding.category}
                      </Badge>
                      <button
                        type="button"
                        onClick={() => onSelectFile?.(finding.file_path)}
                        className="font-mono text-xs text-emerald-700 dark:text-emerald-400 hover:underline flex items-center gap-1 cursor-pointer"
                        title="在代码工作区中打开此文件"
                      >
                        <FileCode className="h-3.5 w-3.5" />
                        <span>
                          {finding.file_path}:{finding.line_number}
                        </span>
                      </button>
                    </div>

                    {finding.refactor_diff_suggestion && onApplyPatch && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onApplyPatch(finding.refactor_diff_suggestion)}
                        className="h-7 text-xs font-serif-academic gap-1 text-emerald-800 dark:text-emerald-300 border-emerald-800/40 hover:bg-emerald-900/10 cursor-pointer"
                        title="将修复补丁填入治理工作区进行审查与应用"
                      >
                        <Wand2 className="h-3 w-3" />
                        <span>填入治理补丁</span>
                      </Button>
                    )}
                  </div>

                  {/* Description & Harm */}
                  <div className="space-y-1.5 text-xs font-serif-academic">
                    <div className="font-medium text-foreground">
                      <span className="font-bold">问题诊断：</span>
                      {finding.issue_description}
                    </div>
                    {finding.harm_analysis && (
                      <div className="text-amber-800 dark:text-amber-300 bg-amber-500/10 p-2 rounded border border-amber-500/20">
                        <span className="font-bold">危害与恶习分析：</span>
                        {finding.harm_analysis}
                      </div>
                    )}
                  </div>

                  {/* Code Snippet */}
                  {finding.code_snippet && (
                    <div className="space-y-1">
                      <span className="text-[11px] font-mono text-muted-foreground">当前代码片段：</span>
                      <pre className="p-2.5 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-foreground/90 border border-border/40">
                        {finding.code_snippet}
                      </pre>
                    </div>
                  )}

                  {/* Refactor Diff Suggestion */}
                  {finding.refactor_diff_suggestion && (
                    <div className="space-y-1">
                      <span className="text-[11px] font-mono text-emerald-700 dark:text-emerald-400 font-semibold">
                        建议修复补丁 (Unified Diff)：
                      </span>
                      <pre className="p-2.5 rounded-lg bg-emerald-950/20 text-xs font-mono overflow-x-auto text-emerald-800 dark:text-emerald-300 border border-emerald-800/30 whitespace-pre-wrap">
                        {finding.refactor_diff_suggestion}
                      </pre>
                    </div>
                  )}
                </div>
              ))
            ) : (
              <div className="p-8 text-center text-muted-foreground text-xs font-serif-academic">
                此分类下暂无问题事项。
              </div>
            )}
          </div>
        </div>
      )}

      {/* SUBTAB 2: MENTAL MODEL & DECISIONS */}
      {activeSubTab === "decisions" && dissectData && (
        <div className="space-y-5">
          {dissectData.mental_model_summary && (
            <div className="p-4 rounded-xl border border-border/80 bg-card space-y-2 shadow-2xs">
              <h4 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                <span>架构心智模型 (Mental Model)</span>
              </h4>
              <div
                className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                dangerouslySetInnerHTML={{
                  __html: renderMarkdownWithMath(dissectData.mental_model_summary),
                }}
              />
            </div>
          )}

          <div className="space-y-3">
            <h4 className="font-serif-academic font-bold text-sm text-foreground">
              核心技术架构决策辨析 ({dissectData.decisions?.length || 0} 组决策)
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {dissectData.decisions?.map((dec, idx) => (
                <div
                  key={idx}
                  className="p-4 rounded-xl border border-border/80 bg-background space-y-2.5 shadow-2xs"
                >
                  <div className="flex items-center justify-between border-b border-border/40 pb-1.5">
                    <span className="font-serif-academic font-bold text-xs text-foreground">
                      {dec.topic}
                    </span>
                    <Badge variant="outline" className="font-mono text-[10px] text-emerald-700 dark:text-emerald-400">
                      采纳: {dec.chosen_solution}
                    </Badge>
                  </div>

                  <p className="text-xs text-foreground/85 font-serif-academic leading-relaxed">
                    <span className="font-semibold text-foreground">核心依据：</span>
                    {dec.core_rationale}
                  </p>

                  {dec.rejected_alternatives && dec.rejected_alternatives.length > 0 && (
                    <div className="text-[11px] text-muted-foreground font-serif-academic">
                      <span className="font-semibold">放弃的备选：</span>
                      {dec.rejected_alternatives.join(", ")}
                    </div>
                  )}

                  {dec.tradeoffs_accepted && (
                    <div className="text-xs p-2 rounded bg-muted/40 border border-border/40 text-foreground/80 font-serif-academic">
                      <span className="font-semibold text-foreground">接受的工程权衡：</span>
                      {dec.tradeoffs_accepted}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* SUBTAB 3: DEPENDENCIES & MECHANISMS */}
      {activeSubTab === "dependencies" && dissectData && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
            {dissectData.dependency_cards?.map((dep, idx) => (
              <div
                key={idx}
                className="p-4 rounded-xl border border-border/80 bg-card space-y-2.5 shadow-2xs"
              >
                <div className="flex items-center justify-between border-b border-border/40 pb-1.5">
                  <span className="font-mono font-bold text-xs text-foreground">
                    📦 {dep.package_name}
                  </span>
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {dep.category}
                  </Badge>
                </div>

                <div className="text-xs font-serif-academic space-y-1.5">
                  <p className="text-foreground/90">
                    <span className="font-semibold">工程角色：</span>
                    {dep.role_in_project}
                  </p>
                  <p className="text-muted-foreground bg-muted/30 p-2 rounded border border-border/40">
                    <span className="font-semibold text-foreground">底层运行机理：</span>
                    {dep.underlying_mechanism}
                  </p>
                  <p className="text-foreground/80 text-[11px]">
                    <span className="font-semibold text-foreground">选用价值：</span>
                    {dep.why_needed}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SUBTAB 4: TOPOLOGY & SEQUENCE */}
      {activeSubTab === "topology" && dissectData && (
        <div className="space-y-5">
          {dissectData.topology_mermaid && (
            <div className="p-4 rounded-xl border border-border/80 bg-card space-y-2 shadow-2xs">
              <h4 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                <Network className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                <span>AST 逆向架构拓扑图 (Mermaid Topology)</span>
              </h4>
              <pre className="p-3.5 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-foreground/90 whitespace-pre-wrap">
                {dissectData.topology_mermaid}
              </pre>
            </div>
          )}

          {dissectData.dataflow_sequence_mermaid && (
            <div className="p-4 rounded-xl border border-border/80 bg-card space-y-2 shadow-2xs">
              <h4 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                <GitBranch className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                <span>数据流与调度时序图 (Dataflow Sequence)</span>
              </h4>
              <pre className="p-3.5 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-foreground/90 whitespace-pre-wrap">
                {dissectData.dataflow_sequence_mermaid}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* SUBTAB 5: WALKTHROUGH 4 STAGES */}
      {activeSubTab === "walkthrough" && dissectData && (
        <div className="space-y-4">
          <div className="space-y-3">
            {dissectData.walkthrough_stages?.map((stage) => (
              <div
                key={stage.stage_index}
                className="p-4 rounded-xl border border-border/80 bg-card space-y-2.5 shadow-2xs"
              >
                <div className="flex items-center justify-between border-b border-border/40 pb-1.5">
                  <div className="flex items-center space-x-2">
                    <Badge variant="outline" className="font-mono text-xs font-bold text-emerald-700 dark:text-emerald-400">
                      Stage {stage.stage_index}
                    </Badge>
                    <span className="font-serif-academic font-bold text-sm text-foreground">
                      {stage.stage_name}
                    </span>
                  </div>
                  <span className="text-xs font-mono text-muted-foreground">{stage.core_theme}</span>
                </div>

                <p className="text-xs text-foreground/85 font-serif-academic leading-relaxed">
                  <span className="font-semibold text-foreground">走查要领与建议：</span>
                  {stage.reading_tips}
                </p>

                {stage.key_files && stage.key_files.length > 0 && (
                  <div className="pt-1">
                    <span className="text-[11px] font-mono text-muted-foreground mr-2">核心源码文件：</span>
                    <div className="inline-flex flex-wrap gap-1.5 pt-1">
                      {stage.key_files.map((file, fIdx) => (
                        <button
                          key={fIdx}
                          type="button"
                          onClick={() => onSelectFile?.(file)}
                          className="text-xs font-mono px-2 py-0.5 rounded bg-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted cursor-pointer transition border border-border/40"
                          title="点击在代码编辑器中打开此文件"
                        >
                          📄 {file}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
