import React, { useState, useEffect } from "react";
import {
  Sparkles,
  Play,
  Zap,
  CheckCircle,
  AlertCircle,
  Copy,
  Check,
  Eye,
  FileText,
  ShieldCheck,
  Wrench,
  Clock,
  Cpu,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { api } from "@/services/api";
import type { SkillSummary, SkillDetail, SkillExecuteResult } from "@/types/workbench";

const CATEGORIES = [
  { id: "all", label: "全部技能" },
  { id: "academic", label: "学术研究" },
  { id: "engineering", label: "工程架构" },
  { id: "writing", label: "学术写作" },
  { id: "custom", label: "自定义技能" },
];

export const SkillsView: React.FC = () => {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [activeSkill, setActiveSkill] = useState<SkillDetail | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  // View Skill Detail State
  const [viewDetailSkill, setViewDetailSkill] = useState<SkillDetail | null>(null);
  const [viewDialogOpen, setViewDialogOpen] = useState(false);
  const [promptCopied, setPromptCopied] = useState(false);

  // Form execution state
  const [formInputs, setFormInputs] = useState<Record<string, any>>({});
  const [executing, setExecuting] = useState(false);
  const [executeResult, setExecuteResult] = useState<SkillExecuteResult | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetchSkills();
  }, []);

  const fetchSkills = async () => {
    setLoading(true);
    try {
      const res = await api.getSkills();
      setSkills(res.items || []);
    } catch (err) {
      console.error("Failed to load skills", err);
    } finally {
      setLoading(false);
    }
  };

  const handleViewSkill = async (skillId: string) => {
    try {
      const detail = await api.getSkill(skillId);
      setViewDetailSkill(detail);
      setViewDialogOpen(true);
    } catch (err: any) {
      alert(`无法获取技能内容: ${err.message}`);
    }
  };

  const handleOpenSkill = async (skillId: string) => {
    try {
      const detail = await api.getSkill(skillId);
      setActiveSkill(detail);
      // Initialize default inputs
      const defaults: Record<string, any> = {};
      const props = detail.input_schema?.properties || {};
      for (const [key, prop] of Object.entries(props)) {
        defaults[key] = prop.default ?? (prop.type === "array" ? "" : "");
      }
      setFormInputs(defaults);
      setExecuteResult(null);
      setDialogOpen(true);
    } catch (err: any) {
      alert(`无法启动技能工作流: ${err.message}`);
    }
  };

  const handleInputChange = (key: string, value: any) => {
    setFormInputs((prev) => ({ ...prev, [key]: value }));
  };

  const handleExecute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSkill) return;

    setExecuting(true);
    setExecuteResult(null);

    // Format inputs based on schema
    const formatted: Record<string, any> = {};
    const props = activeSkill.input_schema?.properties || {};
    for (const [key, val] of Object.entries(formInputs)) {
      const prop = props[key] || {};
      if (prop.type === "array" && typeof val === "string") {
        formatted[key] = val.split(",").map((s) => s.trim()).filter(Boolean);
      } else if ((prop.type === "number" || prop.type === "integer") && typeof val === "string") {
        formatted[key] = Number(val);
      } else {
        formatted[key] = val;
      }
    }

    try {
      const res = await api.executeSkill(activeSkill.skill_id, formatted);
      setExecuteResult(res);
    } catch (err: any) {
      setExecuteResult({
        run_id: "error",
        skill_id: activeSkill.skill_id,
        status: "failed",
        content: "",
        tokens_consumed: 0,
        elapsed_seconds: 0,
        error_message: err.message || "执行异常",
      });
    } finally {
      setExecuting(false);
    }
  };

  const handleCopyOutput = () => {
    if (!executeResult?.content) return;
    navigator.clipboard.writeText(executeResult.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCopyPrompt = () => {
    if (!viewDetailSkill?.prompt_template) return;
    navigator.clipboard.writeText(viewDetailSkill.prompt_template);
    setPromptCopied(true);
    setTimeout(() => setPromptCopied(false), 2000);
  };

  const filteredSkills = selectedCategory === "all"
    ? skills
    : skills.filter((s) => s.category === selectedCategory);

  return (
    <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-6">
      {/* Header Banner */}
      <div className="space-y-1.5 border-b border-border/60 pb-5">
        <div className="flex items-center space-x-2 text-xs font-mono text-emerald-700 dark:text-emerald-400">
          <Sparkles className="h-3.5 w-3.5" />
          <span>Skills Studio · 05</span>
        </div>
        <h1 className="text-2xl sm:text-3xl font-serif-academic font-bold tracking-tight text-foreground">
          学术技能工坊
        </h1>
        <p className="text-xs sm:text-sm text-muted-foreground max-w-2xl font-serif-academic leading-relaxed">
          声明式学术能力与结构化工作流。点击可审查完整 Prompt 指令与规则约束，支持多 Agent 异步并发编排、工具约束与可验证产出。
        </p>
      </div>

      {/* Category Filter Pills */}
      <div className="flex flex-wrap gap-2">
        {CATEGORIES.map((cat) => (
          <button
            key={cat.id}
            type="button"
            onClick={() => setSelectedCategory(cat.id)}
            className={`px-3.5 py-1.5 rounded-full text-xs sm:text-sm font-serif-academic transition-all ${
              selectedCategory === cat.id
                ? "bg-emerald-800 text-white font-medium shadow-xs"
                : "bg-muted/40 text-foreground/75 hover:bg-muted hover:text-foreground"
            }`}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Skill Card Grid */}
      {loading ? (
        <div className="text-center py-16 text-muted-foreground text-xs sm:text-sm font-mono animate-pulse">
          正在加载技能注册表...
        </div>
      ) : filteredSkills.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground text-xs sm:text-sm font-mono">
          暂无该分类下的可用技能
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredSkills.map((skill) => (
            <Card
              key={skill.skill_id}
              className="flex flex-col justify-between hover:border-emerald-800/60 transition top-bevel shadow-xs"
            >
              <CardContent className="p-5 flex-1 flex flex-col justify-between">
                <div>
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <h3
                      className="font-serif-academic font-bold text-sm sm:text-base text-foreground cursor-pointer hover:text-emerald-700 dark:hover:text-emerald-400 transition"
                      onClick={() => handleViewSkill(skill.skill_id)}
                    >
                      {skill.name}
                    </h3>
                    <Badge variant="outline" className="text-xs font-mono shrink-0">
                      {skill.category}
                    </Badge>
                  </div>
                  <p
                    className="text-xs sm:text-sm text-muted-foreground leading-relaxed mb-4 line-clamp-3 cursor-pointer"
                    onClick={() => handleViewSkill(skill.skill_id)}
                  >
                    {skill.description}
                  </p>

                  <div className="space-y-2 mb-4">
                    <div className="text-xs text-muted-foreground/80 font-mono flex items-center justify-between">
                      <span>作者: {skill.author}</span>
                      <span>v{skill.version}</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {(skill.required_tools || []).map((tool) => (
                        <span
                          key={tool}
                          className="px-2 py-0.5 rounded bg-muted/60 text-xs font-mono text-muted-foreground border border-border/40"
                        >
                          {tool}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="pt-3 border-t border-border/60 flex items-center justify-between text-xs font-mono gap-2">
                  <span className="text-muted-foreground text-xs">
                    ≤{skill.default_budget?.max_tokens || 8000} tok
                  </span>
                  <div className="flex items-center space-x-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-8 px-2.5 text-xs font-serif-academic gap-1 border-border/80 hover:bg-muted/60"
                      onClick={() => handleViewSkill(skill.skill_id)}
                    >
                      <Eye className="h-3.5 w-3.5 text-muted-foreground" />
                      <span>查看内容</span>
                    </Button>
                    <Button
                      size="sm"
                      className="h-8 px-3 text-xs font-serif-academic bg-emerald-800 hover:bg-emerald-900 text-white gap-1"
                      onClick={() => handleOpenSkill(skill.skill_id)}
                    >
                      <Play className="h-3.5 w-3.5" />
                      <span>运行</span>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* View Skill Detail & Instructions Modal Dialog */}
      <Dialog open={viewDialogOpen} onOpenChange={setViewDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[88vh] overflow-y-auto">
          {viewDetailSkill && (
            <div className="space-y-5">
              <DialogHeader>
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2 text-xs font-mono text-emerald-700 dark:text-emerald-400">
                    <FileText className="h-4 w-4" />
                    <span>Skill Definition & Prompt Blueprint</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <Badge variant="outline" className="text-xs font-mono">
                      {viewDetailSkill.category}
                    </Badge>
                    <Badge variant="secondary" className="text-xs font-mono">
                      v{viewDetailSkill.version}
                    </Badge>
                  </div>
                </div>
                <DialogTitle className="text-lg sm:text-xl font-serif-academic font-bold pt-1 text-foreground">
                  {viewDetailSkill.name}
                </DialogTitle>
                <DialogDescription className="text-xs sm:text-sm leading-relaxed text-muted-foreground">
                  {viewDetailSkill.description}
                </DialogDescription>
              </DialogHeader>

              {/* Resource Budget Specs */}
              <div className="grid grid-cols-3 gap-3 p-3 rounded-lg bg-muted/30 border border-border/60 text-xs font-mono">
                <div className="flex items-center space-x-2">
                  <Cpu className="h-4 w-4 text-emerald-700 dark:text-emerald-400 shrink-0" />
                  <div>
                    <span className="text-muted-foreground block text-[11px]">最大 Token</span>
                    <strong>{viewDetailSkill.default_budget?.max_tokens || 6000} tok</strong>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <Clock className="h-4 w-4 text-emerald-700 dark:text-emerald-400 shrink-0" />
                  <div>
                    <span className="text-muted-foreground block text-[11px]">预估耗时</span>
                    <strong>{viewDetailSkill.default_budget?.estimated_time_seconds || 30} 秒</strong>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <Zap className="h-4 w-4 text-emerald-700 dark:text-emerald-400 shrink-0" />
                  <div>
                    <span className="text-muted-foreground block text-[11px]">最大步数</span>
                    <strong>{viewDetailSkill.default_budget?.max_steps || 8} 步</strong>
                  </div>
                </div>
              </div>

              {/* Prompt Template Section */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs sm:text-sm font-semibold text-foreground flex items-center gap-1.5">
                    <FileText className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                    系统提示词模板 (Prompt Template)
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs gap-1 text-muted-foreground hover:text-foreground"
                    onClick={handleCopyPrompt}
                  >
                    {promptCopied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
                    <span>{promptCopied ? "已复制 Prompt" : "复制 Prompt"}</span>
                  </Button>
                </div>
                <div className="p-3.5 rounded-lg border border-border/80 bg-muted/40 font-mono text-xs leading-relaxed max-h-60 overflow-y-auto whitespace-pre-wrap text-foreground/90 select-text">
                  {viewDetailSkill.prompt_template || "未声明显式 Prompt 模板"}
                </div>
              </div>

              {/* Rules and Guidelines */}
              {viewDetailSkill.rules && viewDetailSkill.rules.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs sm:text-sm font-semibold text-foreground flex items-center gap-1.5">
                    <ShieldCheck className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                    执行准则与约束规则 (Rules)
                  </span>
                  <ul className="space-y-1.5 p-3 rounded-lg bg-card border border-border/70 text-xs sm:text-sm font-serif-academic text-foreground/85">
                    {viewDetailSkill.rules.map((rule: string, idx: number) => (
                      <li key={idx} className="flex items-start space-x-2">
                        <CheckCircle className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400 shrink-0 mt-0.5" />
                        <span>{rule}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Tools and Schema */}
              <div className="space-y-2">
                <span className="text-xs sm:text-sm font-semibold text-foreground flex items-center gap-1.5">
                  <Wrench className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                  依赖工具与输入参数规范
                </span>
                <div className="flex flex-wrap gap-1.5 pb-1">
                  {(viewDetailSkill.required_tools || []).map((tool) => (
                    <Badge key={tool} variant="outline" className="text-xs font-mono">
                      {tool}
                    </Badge>
                  ))}
                  {(!viewDetailSkill.required_tools || viewDetailSkill.required_tools.length === 0) && (
                    <span className="text-xs text-muted-foreground">无需外部工具调用</span>
                  )}
                </div>

                {viewDetailSkill.input_schema?.properties && (
                  <div className="rounded-lg border border-border/70 overflow-hidden text-xs font-mono">
                    <table className="w-full text-left">
                      <thead className="bg-muted/60 border-b border-border/70">
                        <tr>
                          <th className="p-2 font-medium">参数名</th>
                          <th className="p-2 font-medium">类型</th>
                          <th className="p-2 font-medium">必填</th>
                          <th className="p-2 font-medium">说明</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/50">
                        {Object.entries(viewDetailSkill.input_schema.properties).map(([k, prop]: [string, any]) => {
                          const isReq = (viewDetailSkill.input_schema.required || []).includes(k);
                          return (
                            <tr key={k} className="hover:bg-muted/20">
                              <td className="p-2 font-semibold text-emerald-700 dark:text-emerald-400">{k}</td>
                              <td className="p-2 text-muted-foreground">{prop.type}</td>
                              <td className="p-2">{isReq ? <span className="text-rose-500 font-semibold">是</span> : "否"}</td>
                              <td className="p-2 text-foreground/80 font-sans text-xs">{prop.description || "-"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Actions Footer */}
              <div className="flex items-center justify-between pt-3 border-t border-border/60">
                <span className="text-xs text-muted-foreground font-mono">
                  ID: {viewDetailSkill.skill_id}
                </span>
                <div className="flex items-center space-x-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setViewDialogOpen(false)}
                    className="text-xs font-serif-academic"
                  >
                    关闭
                  </Button>
                  <Button
                    size="sm"
                    className="bg-emerald-800 hover:bg-emerald-900 text-white text-xs font-serif-academic gap-1.5"
                    onClick={() => {
                      setViewDialogOpen(false);
                      handleOpenSkill(viewDetailSkill.skill_id);
                    }}
                  >
                    <Play className="h-3.5 w-3.5" />
                    <span>前往运行此技能</span>
                  </Button>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Skill Runner Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <div className="flex items-center space-x-2 text-xs font-mono text-emerald-700 dark:text-emerald-400">
              <Sparkles className="h-3.5 w-3.5" />
              <span>Skill Execution Studio</span>
            </div>
            <DialogTitle className="text-base sm:text-lg font-serif-academic font-bold">
              {activeSkill?.name}
            </DialogTitle>
            <DialogDescription className="text-xs sm:text-sm">
              {activeSkill?.description}
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleExecute} className="space-y-4 pt-2">
            {activeSkill &&
              Object.entries(activeSkill.input_schema?.properties || {}).map(([key, schema]) => {
                const isReq = (activeSkill.input_schema?.required || []).includes(key);
                const desc = schema.description || "";
                return (
                  <div key={key} className="space-y-1.5 text-xs sm:text-sm">
                    <label className="font-semibold text-foreground flex items-center gap-1">
                      {key} {isReq && <span className="text-rose-500">*</span>}
                      <span className="text-muted-foreground font-normal text-xs">
                        ({desc || (schema.type === "array" ? "逗号分隔数组" : schema.type)})
                      </span>
                    </label>
                    {schema.type === "string" && (key.includes("content") || key.includes("prompt")) ? (
                      <textarea
                        rows={4}
                        required={isReq}
                        value={formInputs[key] || ""}
                        onChange={(e) => handleInputChange(key, e.target.value)}
                        placeholder={desc}
                        className="w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs sm:text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    ) : (
                      <input
                        type={schema.type === "number" || schema.type === "integer" ? "number" : "text"}
                        required={isReq}
                        value={formInputs[key] || ""}
                        onChange={(e) => handleInputChange(key, e.target.value)}
                        placeholder={desc}
                        className="w-full h-9 rounded-md border border-input bg-transparent px-3 py-1 font-mono text-xs sm:text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    )}
                  </div>
                );
              })}

            <div className="flex justify-end pt-2">
              <Button
                type="submit"
                size="sm"
                disabled={executing}
                className="bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic"
              >
                {executing ? (
                  <>
                    <Zap className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                    协同执行中...
                  </>
                ) : (
                  <>
                    <Play className="h-3.5 w-3.5 mr-1.5" />
                    启动执行
                  </>
                )}
              </Button>
            </div>
          </form>

          {/* Execution Result Area */}
          {executeResult && (
            <div className="space-y-3 pt-4 border-t border-border/60">
              <div
                className={`p-3 rounded-md flex items-center justify-between text-xs font-mono ${
                  executeResult.status === "success"
                    ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-400"
                    : "bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400"
                }`}
              >
                <div className="flex items-center space-x-3">
                  {executeResult.status === "success" ? (
                    <CheckCircle className="h-4 w-4" />
                  ) : (
                    <AlertCircle className="h-4 w-4" />
                  )}
                  <span>
                    状态: <strong>{executeResult.status}</strong>
                  </span>
                  <span>·</span>
                  <span>
                    消耗: <strong>{executeResult.tokens_consumed} tok</strong>
                  </span>
                  <span>·</span>
                  <span>
                    耗时: <strong>{executeResult.elapsed_seconds}s</strong>
                  </span>
                </div>
              </div>

              {executeResult.content && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs sm:text-sm font-semibold text-foreground">产出成果报告</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={handleCopyOutput}
                    >
                      {copied ? <Check className="h-3 w-3 mr-1" /> : <Copy className="h-3 w-3 mr-1" />}
                      {copied ? "已复制" : "复制报告"}
                    </Button>
                  </div>
                  <div className="p-4 rounded-md border border-border bg-muted/20 font-serif-academic text-xs sm:text-sm leading-relaxed max-h-72 overflow-y-auto whitespace-pre-wrap select-text">
                    {executeResult.content}
                  </div>
                </div>
              )}

              {executeResult.error_message && (
                <div className="p-3 rounded-md bg-rose-500/10 text-rose-600 text-xs font-mono">
                  错误信息: {executeResult.error_message}
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};
