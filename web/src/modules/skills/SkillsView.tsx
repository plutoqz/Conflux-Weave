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
  Plus,
  Upload,
  FolderUp,
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
import { renderMarkdownWithMath } from "@/lib/math";
import { api } from "@/services/api";
import type {
  SkillSummary,
  SkillDetail,
  SkillExecuteResult,
  ProjectSummary,
  LibraryDocument,
} from "@/types/workbench";

const CATEGORIES = [
  { id: "all", label: "全部技能" },
  { id: "research", label: "学术研究" },
  { id: "governance", label: "工程架构" },
  { id: "writing", label: "学术写作" },
  { id: "utility", label: "实用工具" },
];

const CATEGORY_MAP: Record<string, string> = {
  research: "学术研究",
  governance: "工程架构",
  writing: "学术写作",
  utility: "实用工具",
};

export const SkillsView: React.FC = () => {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [activeSkill, setActiveSkill] = useState<SkillDetail | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  // Object selector data sources (U20)
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [documents, setDocuments] = useState<LibraryDocument[]>([]);

  // View Skill Detail State
  const [viewDetailSkill, setViewDetailSkill] = useState<SkillDetail | null>(null);
  const [viewDialogOpen, setViewDialogOpen] = useState(false);
  const [promptCopied, setPromptCopied] = useState(false);

  // Form execution state
  const [formInputs, setFormInputs] = useState<Record<string, any>>({});
  const [executing, setExecuting] = useState(false);
  const [executeResult, setExecuteResult] = useState<SkillExecuteResult | null>(null);
  const [copied, setCopied] = useState(false);

  // Skill Creation / Import modal state
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createTab, setCreateTab] = useState<"form" | "json">("form");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Form fields
  const [newSkillId, setNewSkillId] = useState("");
  const [newName, setNewName] = useState("");
  const [newCategory, setNewCategory] = useState<"research" | "governance" | "writing" | "utility">("research");
  const [newDescription, setNewDescription] = useState("");
  const [newAuthor, setNewAuthor] = useState("custom");
  const [newVersion, setNewVersion] = useState("1.0.0");
  const [newTools, setNewTools] = useState("retrieval, python_repl");
  const [newPromptTemplate, setNewPromptTemplate] = useState("");
  const [newRules, setNewRules] = useState("必须提供具体依据\n产出需结构化且可验证");
  const [newMaxTokens, setNewMaxTokens] = useState("8000");
  const [newMaxSeconds, setNewMaxSeconds] = useState("180");

  // JSON input
  const [jsonContent, setJsonContent] = useState("");

  const resetCreateForm = () => {
    setNewSkillId("");
    setNewName("");
    setNewCategory("research");
    setNewDescription("");
    setNewAuthor("custom");
    setNewVersion("1.0.0");
    setNewTools("retrieval, python_repl");
    setNewPromptTemplate("");
    setNewRules("必须提供具体依据\n产出需结构化且可验证");
    setNewMaxTokens("8000");
    setNewMaxSeconds("180");
    setJsonContent("");
    setCreateError(null);
  };

  const handleOpenCreate = () => {
    resetCreateForm();
    setCreateTab("form");
    setCreateModalOpen(true);
  };

  const handleOpenImport = () => {
    resetCreateForm();
    setCreateTab("json");
    setJsonContent(
      JSON.stringify(
        {
          skill_id: "custom_paper_insight",
          name: "文献核心洞见提炼",
          category: "research",
          description: "深度剖析收录文献的研究背景、核心方法、实验论证与潜在缺陷",
          author: "custom",
          version: "1.0.0",
          required_tools: ["retrieval", "python_repl"],
          prompt_template:
            "你是一个资深学术分析助手。请深度阅读以下材料，并提炼关键科学问题与方法亮点：\\n\\n{text_content}\\n\\n请按【研究动机与背景】、【创新方法设计】、【实验论证成效】、【边界与局限】四维度给出结构化学术分析。",
          rules: ["必须引用具体实验数据或定理支撑", "客观评价文献的局限性与改进空间"],
          default_budget: {
            max_tokens: 8000,
            max_seconds: 180,
          },
        },
        null,
        2
      )
    );
    setCreateModalOpen(true);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const text = event.target?.result as string;
        JSON.parse(text);
        setJsonContent(text);
        setCreateError(null);
      } catch (err: any) {
        setCreateError(`文件不是合法的 JSON 格式: ${err.message}`);
      }
    };
    reader.readAsText(file);
  };

  const handleSaveSkill = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);

    let payload: any = {};
    if (createTab === "form") {
      if (!newSkillId.trim()) {
        setCreateError("技能唯一标识 (skill_id) 不能为空");
        setCreating(false);
        return;
      }
      if (!newName.trim()) {
        setCreateError("技能名称 (name) 不能为空");
        setCreating(false);
        return;
      }
      if (!newPromptTemplate.trim()) {
        setCreateError("Prompt 模板 (prompt_template) 不能为空");
        setCreating(false);
        return;
      }
      payload = {
        skill_id: newSkillId.trim(),
        name: newName.trim(),
        category: newCategory,
        description: newDescription.trim(),
        author: newAuthor.trim() || "custom",
        version: newVersion.trim() || "1.0.0",
        required_tools: newTools.split(",").map((s) => s.trim()).filter(Boolean),
        prompt_template: newPromptTemplate.trim(),
        rules: newRules.split("\n").map((s) => s.trim()).filter(Boolean),
        default_budget: {
          max_tokens: Number(newMaxTokens) || 8000,
          max_seconds: Number(newMaxSeconds) || 180,
        },
      };
    } else {
      try {
        payload = JSON.parse(jsonContent);
      } catch (err: any) {
        setCreateError(`JSON 语法解析失败: ${err.message}`);
        setCreating(false);
        return;
      }
      if (!payload.skill_id || !payload.name || !payload.prompt_template) {
        setCreateError("JSON 必须包含 skill_id、name 和 prompt_template 字段");
        setCreating(false);
        return;
      }
    }

    try {
      await api.createSkill(payload);
      setCreateModalOpen(false);
      await fetchSkills();
    } catch (err: any) {
      setCreateError(err.message || "创建或导入技能失败");
    } finally {
      setCreating(false);
    }
  };

  useEffect(() => {
    fetchSkills();
    loadProjectsAndDocs();
  }, []);

  const loadProjectsAndDocs = async () => {
    try {
      const [projRes, docRes] = await Promise.allSettled([
        api.getProjects(),
        api.getDocuments("active"),
      ]);
      if (projRes.status === "fulfilled") {
        setProjects(projRes.value || []);
      }
      if (docRes.status === "fulfilled") {
        setDocuments(docRes.value?.items || []);
      }
    } catch {
      // ignore
    }
  };

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
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border/60 pb-5">
        <div className="space-y-1.5">
          <div className="flex items-center space-x-2 text-xs font-mono text-emerald-700 dark:text-emerald-400">
            <Sparkles className="h-3.5 w-3.5" />
            <span>Skills Studio · 05</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-serif-academic font-bold tracking-tight text-foreground">
            学术技能工坊
          </h1>
          <p className="text-xs sm:text-sm text-muted-foreground max-w-2xl font-serif-academic leading-relaxed">
            声明式学术能力与结构化工作流。支持新建自定义技能、导入标准化 JSON 技能配置，审查完整 Prompt 指令与规则约束。
          </p>
        </div>

        <div className="flex items-center space-x-2.5 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={handleOpenImport}
            className="h-9 px-3 text-xs font-serif-academic gap-1.5 border-border/80 hover:bg-muted/60 cursor-pointer"
          >
            <Upload className="h-3.5 w-3.5 text-muted-foreground" />
            <span>导入技能</span>
          </Button>
          <Button
            size="sm"
            onClick={handleOpenCreate}
            className="h-9 px-3.5 text-xs font-serif-academic bg-emerald-800 hover:bg-emerald-700 text-white gap-1.5 shadow-xs cursor-pointer"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>新建技能</span>
          </Button>
        </div>
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
                      {CATEGORY_MAP[skill.category] || skill.category}
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

                // Project selector (U20)
                if (key === "project_id") {
                  return (
                    <div key={key} className="space-y-1.5 text-xs sm:text-sm">
                      <label className="font-semibold text-foreground flex items-center gap-1">
                        目标工程项目 (project_id) {isReq && <span className="text-rose-500">*</span>}
                        <span className="text-muted-foreground font-normal text-xs">
                          ({desc || "选择目标代码库"})
                        </span>
                      </label>
                      <select
                        required={isReq}
                        value={formInputs[key] || ""}
                        onChange={(e) => handleInputChange(key, e.target.value)}
                        className="w-full h-9 rounded-md border border-input bg-card px-3 py-1 font-mono text-xs sm:text-sm focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                      >
                        <option value="">-- 请选择目标工程 --</option>
                        {projects.map((p) => (
                          <option key={p.project_id} value={p.project_id}>
                            {p.name} ({p.project_id.slice(0, 8)})
                          </option>
                        ))}
                      </select>
                      {projects.length === 0 && (
                        <p className="text-[11px] text-amber-600 font-serif-academic">
                          提示：当前暂无工程项目，可先在“项目与代码”模块添加工程根目录。
                        </p>
                      )}
                    </div>
                  );
                }

                // Paper / Document selector (U20)
                if (key === "paper_ids" || key === "paper_id") {
                  const isArray = key === "paper_ids" || schema.type === "array";
                  return (
                    <div key={key} className="space-y-1.5 text-xs sm:text-sm">
                      <label className="font-semibold text-foreground flex items-center gap-1">
                        {key === "paper_ids" ? "关联文献列表 (paper_ids)" : "目标文献 (paper_id)"}{" "}
                        {isReq && <span className="text-rose-500">*</span>}
                        <span className="text-muted-foreground font-normal text-xs">
                          ({desc || "从知识库点选或手动输入ID"})
                        </span>
                      </label>
                      {documents.length > 0 && (
                        <select
                          value=""
                          onChange={(e) => {
                            const val = e.target.value;
                            if (!val) return;
                            if (isArray) {
                              const cur = formInputs[key];
                              const parts = typeof cur === "string" && cur.trim()
                                ? cur.split(",").map((s: string) => s.trim()).filter(Boolean)
                                : Array.isArray(cur) ? [...cur] : [];
                              if (!parts.includes(val)) {
                                handleInputChange(key, [...parts, val].join(", "));
                              }
                            } else {
                              handleInputChange(key, val);
                            }
                          }}
                          className="w-full h-8 rounded border border-input bg-card px-2.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                        >
                          <option value="">-- 从文献库快速插入文献 ID --</option>
                          {documents.map((d) => (
                            <option key={d.document_id} value={d.document_id}>
                              {d.title} ({d.document_id.slice(0, 8)})
                            </option>
                          ))}
                        </select>
                      )}
                      <input
                        type="text"
                        required={isReq}
                        value={Array.isArray(formInputs[key]) ? formInputs[key].join(", ") : (formInputs[key] || "")}
                        onChange={(e) => handleInputChange(key, e.target.value)}
                        placeholder={isArray ? "文献ID列表，逗号分隔，如 doc-1, doc-2" : "输入或点选文献ID"}
                        className="w-full h-9 rounded-md border border-input bg-transparent px-3 py-1 font-mono text-xs sm:text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                      />
                    </div>
                  );
                }

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
          {executeResult && (() => {
            const isSuccess = executeResult.status === "success" || executeResult.status === "completed";
            return (
              <div className="space-y-3 pt-4 border-t border-border/60">
                <div
                  className={`p-3 rounded-md flex items-center justify-between text-xs font-mono ${
                    isSuccess
                      ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-400"
                      : "bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400"
                  }`}
                >
                  <div className="flex items-center space-x-3">
                    {isSuccess ? (
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

                {/* Tool Traces Section */}
                {executeResult.tool_traces && executeResult.tool_traces.length > 0 && (
                  <div className="space-y-2">
                    <div className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                      <Wrench className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                      <span>真实工具调用链路与轨迹 (Tool Traces · {executeResult.tool_traces.length} 步)</span>
                    </div>
                    <div className="space-y-1.5 rounded-lg border border-border/70 p-2.5 bg-muted/20 text-xs font-mono">
                      {executeResult.tool_traces.map((trace: any, idx: number) => {
                        const toolName = trace.tool || trace.tool_name || "tool";
                        const summary = trace.summary || trace.input_summary || "";
                        return (
                          <div
                            key={idx}
                            className="flex flex-wrap items-center justify-between py-1.5 px-2.5 rounded bg-background/70 border border-border/40 gap-2"
                          >
                            <div className="flex items-center space-x-2 truncate">
                              <span className="text-muted-foreground text-[11px]">#{trace.step ?? idx + 1}</span>
                              <span className="font-semibold text-emerald-800 dark:text-emerald-300">
                                {toolName}
                              </span>
                              {summary && (
                                <span className="text-muted-foreground truncate max-w-xs sm:max-w-md text-[11px]">
                                  {summary}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center space-x-2 shrink-0">
                              {trace.duration_seconds !== undefined && (
                                <span className="text-muted-foreground text-[10px]">
                                  {trace.duration_seconds}s
                                </span>
                              )}
                              <Badge
                                variant={trace.status === "success" ? "default" : "destructive"}
                                className="text-[10px] px-1.5 py-0 font-mono"
                              >
                                {trace.status}
                              </Badge>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Artifacts Section */}
                {executeResult.artifacts && executeResult.artifacts.length > 0 && (
                  <div className="space-y-2">
                    <div className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                      <FileText className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                      <span>持久化产物与审计报告 (Artifacts · {executeResult.artifacts.length} 份)</span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {executeResult.artifacts.map((art: any, idx: number) => (
                        <div
                          key={idx}
                          className="p-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 text-xs font-mono flex items-center justify-between"
                        >
                          <div className="truncate mr-2">
                            <div className="font-semibold text-foreground truncate">{art.name}</div>
                            <div className="text-[10px] text-muted-foreground truncate font-mono">
                              ID: {art.artifact_id || art.path}
                            </div>
                          </div>
                          <Badge variant="outline" className="text-[10px] shrink-0 font-mono">
                            {art.media_type || art.type || "artifact"}
                          </Badge>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Content / Report */}
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
                    <div
                      className="p-4 rounded-md border border-border bg-muted/20 font-serif-academic text-xs sm:text-sm leading-relaxed max-h-72 overflow-y-auto whitespace-pre-wrap select-text prose dark:prose-invert max-w-none"
                      dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(executeResult.content) }}
                    />
                  </div>
                )}

                {(executeResult.error_message || executeResult.error) && (
                  <div className="p-3 rounded-md bg-rose-500/10 text-rose-600 text-xs font-mono">
                    错误信息: {executeResult.error_message || executeResult.error}
                  </div>
                )}
              </div>
            );
          })()}
        </DialogContent>
      </Dialog>

      {/* Create / Import Skill Modal Dialog */}
      <Dialog open={createModalOpen} onOpenChange={setCreateModalOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <div className="flex items-center space-x-2 text-xs font-mono text-emerald-700 dark:text-emerald-400">
              <Sparkles className="h-4 w-4" />
              <span>Skills Studio · Definition & Registration</span>
            </div>
            <DialogTitle className="text-lg sm:text-xl font-serif-academic font-bold pt-1 text-foreground">
              {createTab === "form" ? "新建学术技能" : "导入技能配置"}
            </DialogTitle>
            <DialogDescription className="text-xs sm:text-sm text-muted-foreground font-serif-academic">
              注册新的声明式能力或导入外部标准化 Prompt 蓝图与验证约束。
            </DialogDescription>
          </DialogHeader>

          {/* Mode Switch Tabs */}
          <div className="flex border-b border-border/70 pb-2 gap-2">
            <button
              type="button"
              onClick={() => setCreateTab("form")}
              className={`px-3 py-1.5 text-xs font-serif-academic rounded-md transition cursor-pointer ${
                createTab === "form"
                  ? "bg-muted font-bold text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              表单可视化配置
            </button>
            <button
              type="button"
              onClick={() => setCreateTab("json")}
              className={`px-3 py-1.5 text-xs font-serif-academic rounded-md transition cursor-pointer ${
                createTab === "json"
                  ? "bg-muted font-bold text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              JSON 导入 / 粘贴
            </button>
          </div>

          <form onSubmit={handleSaveSkill} className="space-y-4 pt-1">
            {createError && (
              <div className="p-3 rounded-md bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-mono flex items-center gap-2">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>{createError}</span>
              </div>
            )}

            {createTab === "form" ? (
              <div className="space-y-3.5 text-xs">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="font-semibold text-foreground">
                      技能唯一标识 (ID) <span className="text-rose-500">*</span>
                    </label>
                    <input
                      type="text"
                      placeholder="e.g. academic_paper_audit"
                      value={newSkillId}
                      onChange={(e) => setNewSkillId(e.target.value)}
                      className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                      required
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="font-semibold text-foreground">
                      技能名称 <span className="text-rose-500">*</span>
                    </label>
                    <input
                      type="text"
                      placeholder="e.g. 论文深度审计"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-serif-academic text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                      required
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div className="space-y-1">
                    <label className="font-semibold text-foreground">所属分类</label>
                    <select
                      value={newCategory}
                      onChange={(e) => setNewCategory(e.target.value as any)}
                      className="w-full h-8 px-2 rounded-md border border-border/80 bg-background text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                    >
                      <option value="research">学术研究 (research)</option>
                      <option value="governance">工程架构 (governance)</option>
                      <option value="writing">学术写作 (writing)</option>
                      <option value="utility">实用工具 (utility)</option>
                    </select>
                  </div>
                  <div className="space-y-1">
                    <label className="font-semibold text-foreground">作者</label>
                    <input
                      type="text"
                      value={newAuthor}
                      onChange={(e) => setNewAuthor(e.target.value)}
                      className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="font-semibold text-foreground">版本号</label>
                    <input
                      type="text"
                      value={newVersion}
                      onChange={(e) => setNewVersion(e.target.value)}
                      className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                    />
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="font-semibold text-foreground">描述说明</label>
                  <input
                    type="text"
                    placeholder="简要概括该技能的功能与适用场景"
                    value={newDescription}
                    onChange={(e) => setNewDescription(e.target.value)}
                    className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-serif-academic text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                  />
                </div>

                <div className="space-y-1">
                  <label className="font-semibold text-foreground">
                    所需工具 (逗号分隔)
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. retrieval, python_repl, academic_search"
                    value={newTools}
                    onChange={(e) => setNewTools(e.target.value)}
                    className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                  />
                </div>

                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <label className="font-semibold text-foreground">
                      Prompt 蓝图模板 <span className="text-rose-500">*</span>
                    </label>
                    <span className="text-[10px] text-muted-foreground font-mono">
                      支持 &#123;input&#125; 等变量占位
                    </span>
                  </div>
                  <textarea
                    rows={4}
                    placeholder="输入 Prompt 蓝图模板，例如：请对以下材料进行严谨的同行评议：&#10;&#10;{text_content}"
                    value={newPromptTemplate}
                    onChange={(e) => setNewPromptTemplate(e.target.value)}
                    className="w-full p-2.5 rounded-md border border-border/80 bg-background font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700 leading-relaxed"
                    required
                  />
                </div>

                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <label className="font-semibold text-foreground">规则与约束</label>
                    <span className="text-[10px] text-muted-foreground font-mono">
                      每行一条约束规则
                    </span>
                  </div>
                  <textarea
                    rows={2}
                    value={newRules}
                    onChange={(e) => setNewRules(e.target.value)}
                    className="w-full p-2 rounded-md border border-border/80 bg-background font-serif-academic text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="font-semibold text-foreground">最大 Token 预算</label>
                    <input
                      type="number"
                      value={newMaxTokens}
                      onChange={(e) => setNewMaxTokens(e.target.value)}
                      className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="font-semibold text-foreground">超时限制 (秒)</label>
                    <input
                      type="number"
                      value={newMaxSeconds}
                      onChange={(e) => setNewMaxSeconds(e.target.value)}
                      className="w-full h-8 px-2.5 rounded-md border border-border/80 bg-background font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700"
                    />
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-3 text-xs">
                <div className="flex items-center justify-between">
                  <label className="font-semibold text-foreground">技能 JSON 定义规范</label>
                  <label className="cursor-pointer inline-flex items-center gap-1 text-[11px] font-mono text-emerald-800 dark:text-emerald-300 hover:underline">
                    <FolderUp className="h-3.5 w-3.5" />
                    <span>选择 JSON 文件上传</span>
                    <input
                      type="file"
                      accept=".json"
                      onChange={handleFileUpload}
                      className="hidden"
                    />
                  </label>
                </div>
                <textarea
                  rows={12}
                  value={jsonContent}
                  onChange={(e) => setJsonContent(e.target.value)}
                  placeholder="在此处粘贴完整 JSON 技能配置..."
                  className="w-full p-3 rounded-md border border-border/80 bg-muted/20 font-mono text-xs focus:outline-hidden focus:ring-1 focus:ring-emerald-700 leading-relaxed"
                  required
                />
              </div>
            )}

            <div className="pt-3 border-t border-border/60 flex items-center justify-end space-x-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setCreateModalOpen(false)}
                disabled={creating}
                className="text-xs font-serif-academic cursor-pointer"
              >
                取消
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={creating}
                className="text-xs font-serif-academic bg-emerald-800 hover:bg-emerald-900 text-white min-w-[80px] cursor-pointer"
              >
                {creating ? "保存中..." : createTab === "form" ? "创建技能" : "导入配置"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
};
