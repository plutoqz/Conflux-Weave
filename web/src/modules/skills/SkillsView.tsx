import React, { useState, useEffect } from "react";
import { Sparkles, Play, Zap, CheckCircle, AlertCircle, Copy, Check } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
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
      alert(`无法获取技能详情: ${err.message}`);
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

  const filteredSkills = selectedCategory === "all"
    ? skills
    : skills.filter((s) => s.category === selectedCategory);

  return (
    <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-6">
      {/* Header Banner */}
      <div className="space-y-1.5 border-b border-border/60 pb-5">
        <div className="flex items-center space-x-2 text-xs font-mono text-emerald-600 dark:text-emerald-400">
          <Sparkles className="h-3.5 w-3.5" />
          <span>Skills Studio · 05</span>
        </div>
        <h1 className="text-2xl font-serif-academic font-bold tracking-tight text-foreground">
          学术技能工作坊
        </h1>
        <p className="text-xs text-muted-foreground max-w-2xl font-serif-academic">
          声明式学术能力与结构化工作流。开箱即用，支持多 Agent 异步并发编排、工具约束与可验证产出。
        </p>
      </div>

      {/* Category Filter Pills */}
      <div className="flex flex-wrap gap-2">
        {CATEGORIES.map((cat) => (
          <button
            key={cat.id}
            type="button"
            onClick={() => setSelectedCategory(cat.id)}
            className={`px-3 py-1.5 rounded-full text-xs font-serif-academic transition-all ${
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
        <div className="text-center py-16 text-muted-foreground text-xs font-mono animate-pulse">
          正在加载技能注册表...
        </div>
      ) : filteredSkills.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground text-xs font-mono">
          暂无该分类下的可用技能
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredSkills.map((skill) => (
            <Card
              key={skill.skill_id}
              className="flex flex-col justify-between hover:border-emerald-800/40 transition top-bevel"
            >
              <CardContent className="p-5 flex-1 flex flex-col justify-between">
                <div>
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <h3 className="font-serif-academic font-bold text-sm text-foreground">
                      {skill.name}
                    </h3>
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {skill.category}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed mb-4 line-clamp-3">
                    {skill.description}
                  </p>

                  <div className="space-y-2 mb-4">
                    <div className="text-[11px] text-muted-foreground/80 font-mono flex items-center justify-between">
                      <span>作者: {skill.author}</span>
                      <span>v${skill.version}</span>
                    </div>
                    <div className="flex flex-wrap gap-1 pt-1">
                      {(skill.required_tools || []).map((tool) => (
                        <span
                          key={tool}
                          className="px-1.5 py-0.5 rounded bg-muted/60 text-[10px] font-mono text-muted-foreground"
                        >
                          {tool}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="pt-3 border-t border-border/60 flex items-center justify-between text-xs font-mono">
                  <span className="text-muted-foreground text-[11px]">
                    ≤{skill.default_budget?.max_tokens || 8000} tok
                  </span>
                  <Button
                    size="sm"
                    className="h-7 text-xs font-serif-academic"
                    onClick={() => handleOpenSkill(skill.skill_id)}
                  >
                    <Play className="h-3 w-3 mr-1" />
                    运行工作流
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Skill Runner Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <div className="flex items-center space-x-2 text-xs font-mono text-emerald-600 dark:text-emerald-400">
              <Sparkles className="h-3.5 w-3.5" />
              <span>Skill Execution Studio</span>
            </div>
            <DialogTitle className="text-base font-serif-academic font-bold">
              {activeSkill?.name}
            </DialogTitle>
            <DialogDescription className="text-xs">
              {activeSkill?.description}
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleExecute} className="space-y-4 pt-2">
            {activeSkill &&
              Object.entries(activeSkill.input_schema?.properties || {}).map(([key, schema]) => {
                const isReq = (activeSkill.input_schema?.required || []).includes(key);
                const desc = schema.description || "";
                return (
                  <div key={key} className="space-y-1.5 text-xs">
                    <label className="font-semibold text-foreground flex items-center gap-1">
                      {key} {isReq && <span className="text-rose-500">*</span>}
                      <span className="text-muted-foreground font-normal text-[11px]">
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
                        className="w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    ) : (
                      <input
                        type={schema.type === "number" || schema.type === "integer" ? "number" : "text"}
                        required={isReq}
                        value={formInputs[key] || ""}
                        onChange={(e) => handleInputChange(key, e.target.value)}
                        placeholder={desc}
                        className="w-full h-9 rounded-md border border-input bg-transparent px-3 py-1 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    )}
                  </div>
                );
              })}

            <div className="flex justify-end pt-2">
              <Button type="submit" size="sm" disabled={executing}>
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

          {/* Metrics Banner */}
          {executeResult && (
            <div className="space-y-3 pt-4 border-t border-border/60">
              <div
                className={`p-3 rounded-md flex items-center justify-between text-xs font-mono ${
                  executeResult.status === "success"
                    ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
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

              {/* Output Content */}
              {executeResult.content && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">产出成果报告</span>
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
                  <div className="p-4 rounded-md border border-border bg-muted/20 font-serif-academic text-xs leading-relaxed max-h-72 overflow-y-auto whitespace-pre-wrap">
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
