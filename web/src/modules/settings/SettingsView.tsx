import React, { useState, useEffect } from "react";
import {
  Cpu,
  Bookmark,
  Trash2,
  Server,
  RefreshCw,
  Plus,
  Copy,
  Check,
  Type,
  Layers,
  Sparkles,
  Search,
  ShieldCheck,
  Image as ImageIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { useWorkbenchStore, type FontSizePreference } from "@/stores/useWorkbenchStore";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { MCPServer } from "@/types/workbench";

export const SettingsView: React.FC = () => {
  const { fontSize, setFontSize } = useWorkbenchStore();
  const [provider, setProvider] = useState("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [rerankerModel, setRerankerModel] = useState("");
  const [engineModel, setEngineModel] = useState("");
  const [effective, setEffective] = useState<any>(null);
  const [memories, setMemories] = useState<any[]>([]);
  const [recallQuery, setRecallQuery] = useState("");
  const [recallResult, setRecallResult] = useState<any[] | null>(null);
  const [recallBusy, setRecallBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [statusOk, setStatusOk] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [testOk, setTestOk] = useState(false);

  // MCP Gateway State
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [mcpDialogOpen, setMcpDialogOpen] = useState(false);
  const [mcpSnippetTab, setMcpSnippetTab] = useState<"sse" | "stdio">("sse");
  const [mcpCopied, setMcpCopied] = useState(false);
  const [syncingServerId, setSyncingServerId] = useState<string | null>(null);

  // New MCP Server Form State
  const [newServerId, setNewServerId] = useState("");
  const [newServerName, setNewServerName] = useState("");
  const [newServerTransport, setNewServerTransport] = useState<"stdio" | "sse">("stdio");
  const [newServerCommand, setNewServerCommand] = useState("");
  const [newServerArgs, setNewServerArgs] = useState("");
  const [newServerUrl, setNewServerUrl] = useState("");
  const [newServerError, setNewServerError] = useState<string | null>(null);

  const loadConfig = () => {
    return api.getConfig().then((cfg) => {
      if (cfg?.provider) {
        setProvider(cfg.provider.name || "openai");
        setBaseUrl(cfg.provider.base_url || "");
        setModel(cfg.provider.model || "");
        setEmbeddingModel(cfg.provider.embedding_model || "");
        setRerankerModel(cfg.provider.reranker_model || "");
        setEngineModel(cfg.provider.engine_model || "");
      }
      // A5：当前进程生效值（启动时装配）；保存后、重启前与持久值不同。
      setEffective(cfg?.provider_effective ?? null);
    }).catch(() => {});
  };

  useEffect(() => {
    loadConfig();

    api.getMemories().then((res) => setMemories(res.items || [])).catch(() => {});
    fetchMcpServers();
  }, []);

  const handleRecallPreview = async () => {
    if (!recallQuery.trim() || recallBusy) return;
    setRecallBusy(true);
    try {
      const res = await api.recallMemories(recallQuery.trim());
      setRecallResult(res.items || []);
    } catch {
      setRecallResult([]);
    } finally {
      setRecallBusy(false);
    }
  };

  const fetchMcpServers = async () => {
    setMcpLoading(true);
    try {
      const res = await api.getMcpServers();
      setMcpServers(res.items || []);
    } catch (e) {
      console.error("Failed to load MCP servers", e);
    } finally {
      setMcpLoading(false);
    }
  };

  const handleSyncMcp = async (serverId: string) => {
    setSyncingServerId(serverId);
    try {
      await api.syncMcpServer(serverId);
      await fetchMcpServers();
    } catch (e: any) {
      alert(`同步失败: ${e.message}`);
    } finally {
      setSyncingServerId(null);
    }
  };

  const handleDeleteMcp = async (serverId: string) => {
    if (!confirm(`确定移除 MCP Server ${serverId} 吗？`)) return;
    try {
      await api.deleteMcpServer(serverId);
      setMcpServers((prev) => prev.filter((s) => s.server_id !== serverId));
    } catch (e: any) {
      alert(`删除失败: ${e.message}`);
    }
  };

  const handleRegisterMcp = async (e: React.FormEvent) => {
    e.preventDefault();
    setNewServerError(null);
    try {
      await api.registerMcpServer({
        server_id: newServerId.trim(),
        name: newServerName.trim(),
        transport: newServerTransport,
        command: newServerTransport === "stdio" ? newServerCommand.trim() : undefined,
        args: newServerTransport === "stdio" && newServerArgs.trim() ? newServerArgs.trim().split(/\s+/) : undefined,
        url: newServerTransport === "sse" ? newServerUrl.trim() : undefined,
      });
      setMcpDialogOpen(false);
      setNewServerId("");
      setNewServerName("");
      setNewServerCommand("");
      setNewServerArgs("");
      setNewServerUrl("");
      await fetchMcpServers();
    } catch (e: any) {
      setNewServerError(e.message || "接入失败");
    }
  };

  const handleSaveProvider = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setStatusMsg(null);
    try {
      const res = await api.updateProviderConfig({
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || undefined,
        model: model.trim(),
        embedding_model: embeddingModel.trim() || undefined,
        reranker_model: rerankerModel.trim() || undefined,
        engine_model: engineModel.trim() || undefined,
      });
      // A5 反馈三要素之二：生效方式。保存写入持久层（dotenv），进程内适配器
      // 仍是启动时装配的旧值，requires_restart=true 时需重启服务才生效。
      setStatusOk(true);
      setStatusMsg(
        res.requires_restart
          ? `${res.message || "配置已保存。"}当前进程仍使用启动时的配置，重启服务后生效。`
          : `${res.message || "配置已保存。"}立即生效。`
      );
      await loadConfig();
    } catch (e: any) {
      setStatusOk(false);
      setStatusMsg(`保存失败: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleTestProvider = async () => {
    if (testing) return;
    setTesting(true);
    setTestMsg(null);
    try {
      const res = await api.testProviderConfig({
        base_url: baseUrl.trim() || undefined,
        api_key: apiKey.trim() || undefined,
        model: model.trim() || undefined,
        embedding_model: embeddingModel.trim() || undefined,
      });
      setTestOk(res.ok);
      const parts = [
        `${res.ok ? "✓ Chat 连通" : "✗ Chat 失败"}${res.latency_ms != null ? ` (${res.latency_ms}ms)` : ""} — ${res.message}`,
      ];
      if (res.embedding?.attempted) {
        parts.push(
          res.embedding.ok
            ? `✓ Embedding 连通 (${res.embedding.latency_ms}ms, ${res.embedding.dimensions} 维${res.embedding.input_tokens != null ? `, ${res.embedding.input_tokens} tokens` : ""})`
            : `✗ Embedding 失败 — ${res.embedding.message}`
        );
      }
      setTestMsg(parts.join("；"));
    } catch (e: any) {
      setTestOk(false);
      setTestMsg(`测试失败: ${e.message}`);
    } finally {
      setTesting(false);
    }
  };

  const handleDeleteMemory = async (id: string) => {
    try {
      await api.deleteMemory(id);
      setMemories(memories.filter((m) => m.memory_id !== id));
    } catch (e: any) {
      alert(`删除失败: ${e.message}`);
    }
  };

  return (
    <div className="max-w-4xl mx-auto p-4 sm:p-6 space-y-8">
      {/* Appearance & Typography Preferences */}
      <Card>
        <CardHeader>
          <div className="flex items-center space-x-2">
            <Type className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
            <div>
              <CardTitle className="text-base">界面显示与字体字号偏好</CardTitle>
              <CardDescription className="text-xs">
                根据个人阅读习惯与屏幕分辨率自由调整工作台文字字号。设置将即时全站生效并自动记忆。
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
            {[
              { id: "normal", label: "标准 (14px)", desc: "紧凑高密度布局" },
              { id: "medium", label: "舒适 (15.5px)", desc: "推荐日常与精读" },
              { id: "large", label: "大号 (17px)", desc: "清晰易读护眼" },
              { id: "xlarge", label: "特大 (18.5px)", desc: "高分大屏无障碍" },
            ].map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => setFontSize(opt.id as FontSizePreference)}
                className={`p-3 rounded-xl border text-left transition cursor-pointer flex flex-col justify-between ${
                  fontSize === opt.id
                    ? "border-emerald-700 dark:border-emerald-400 bg-emerald-500/10 text-foreground font-semibold shadow-xs"
                    : "border-border bg-card/60 hover:bg-muted/50 text-foreground/80"
                }`}
              >
                <div className="flex items-center justify-between w-full mb-1">
                  <span className="text-sm">{opt.label}</span>
                  {fontSize === opt.id && (
                    <Badge variant="default" className="text-[10px] px-1 py-0 bg-emerald-700">
                      当前
                    </Badge>
                  )}
                </div>
                <span className="text-xs text-muted-foreground">{opt.desc}</span>
              </button>
            ))}
          </div>
          <div className="p-3.5 rounded-lg border border-border/70 bg-muted/30 flex items-center justify-between text-xs">
            <span className="text-muted-foreground font-serif-academic">
              字号实时预览：系统采用确定性沙箱闭环，所有研读分析与项目导学均支持异步并发调度。
            </span>
            <span className="font-mono text-emerald-700 dark:text-emerald-400 font-semibold shrink-0 ml-3">
              当前层级: {fontSize.toUpperCase()}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Provider Config Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center space-x-2">
            <Cpu className="h-5 w-5 text-emerald-500" />
            <div>
              <CardTitle className="text-base">LLM 模型与 Provider 设置</CardTitle>
              <CardDescription className="text-xs">
                配置大语言模型服务提供商。支持 OpenAI 兼容格式、Ollama 本地大模型及自定义中继。
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <form id="provider-form" onSubmit={handleSaveProvider} className="space-y-4 text-xs">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="font-medium text-foreground">Provider 协议类型</label>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  className="w-full h-9 rounded-md border border-input bg-transparent px-3 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="openai">OpenAI Compatible (官方 / 代理 / 中继)</option>
                  <option value="ollama">Ollama (本地部署)</option>
                  <option value="gemini">Google Gemini</option>
                  <option value="anthropic">Anthropic Claude</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <label className="font-medium text-foreground">模型名称 (Model Identifier)</label>
                <Input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="例如：gpt-4o, claude-3-5-sonnet-20241022, qwen2.5:32b"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="font-medium text-foreground">Embedding 模型 (记忆语义召回 / 检索向量)</label>
                <Input
                  value={embeddingModel}
                  onChange={(e) => setEmbeddingModel(e.target.value)}
                  placeholder="例如：embedding-3（留空则记忆召回退回 recency 兜底）"
                />
              </div>
              <div className="space-y-1.5">
                <label className="font-medium text-foreground">Reranker 模型 (检索重排，可选)</label>
                <Input
                  value={rerankerModel}
                  onChange={(e) => setRerankerModel(e.target.value)}
                  placeholder="例如：qwen3-rerank"
                />
              </div>
              <div className="space-y-1.5">
                <label className="font-medium text-foreground">深度研究引擎模型 (Engine，可选)</label>
                <Input
                  value={engineModel}
                  onChange={(e) => setEngineModel(e.target.value)}
                  placeholder="留空回退到 Chat 模型"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="font-medium text-foreground">API Base URL (留空使用默认地址)</label>
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.openai.com/v1 或 http://localhost:11434/v1"
              />
            </div>

            <div className="space-y-1.5">
              <label className="font-medium text-foreground">API Key</label>
              <Input
                id="cfg-api-key"
                name="api_key"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={effective?.api_key_configured ? "已配置（输入可覆盖）" : "••••••••••••••••••••••••"}
              />
            </div>

            {statusMsg && (
              <div
                className={`p-2.5 rounded-md border font-medium ${
                  statusOk
                    ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
                    : "bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-400"
                }`}
              >
                {statusMsg}
              </div>
            )}

            {testMsg && (
              <div
                className={`p-2.5 rounded-md border font-medium whitespace-pre-wrap ${
                  testOk
                    ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
                    : "bg-amber-500/10 border-amber-500/30 text-amber-600 dark:text-amber-400"
                }`}
              >
                {testMsg}
              </div>
            )}

            {/* A5 反馈三要素之三：当前进程生效值（与上方持久值对比，判断是否需要重启）。 */}
            <div className="p-2.5 rounded-md border border-border/60 bg-muted/20 text-[11px] font-mono space-y-0.5">
              <p className="font-sans font-semibold text-foreground">当前进程生效值（启动时装配）</p>
              {effective ? (
                <>
                  <p className="text-muted-foreground">model: {effective.model || "—"}{effective.api_key_configured ? " · key ✓" : " · key ✗"}</p>
                  <p className="text-muted-foreground">embedding: {effective.embedding_model || "—（recency 兜底）"}</p>
                  <p className="text-muted-foreground">reranker: {effective.reranker_model || "—"} · engine: {effective.engine_model || "—"}</p>
                  <p className="text-muted-foreground">base_url: {effective.base_url || "—"}</p>
                </>
              ) : (
                <p className="text-muted-foreground">进程生效配置不可用（Provider 未装配）。</p>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" size="sm" disabled={testing || saving} onClick={handleTestProvider}>
                {testing ? "测试中…" : "测试连通性"}
              </Button>
              <Button type="submit" size="sm" disabled={saving}>
                {saving ? "正在保存..." : "保存配置"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Task Capability Matrix (U17) */}
      <Card>
        <CardHeader>
          <div className="flex items-center space-x-2">
            <Layers className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
            <div>
              <CardTitle className="text-base">核心能力与任务就绪矩阵 (Task Capability Matrix)</CardTitle>
              <CardDescription className="text-xs">
                针对各业务任务模块的前置依赖与当前服务进程的实际就绪情况自检，清晰呈现降级行为。
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* 1. Chat */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 flex flex-col justify-between space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 font-medium text-xs">
                  <Sparkles className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                  <span className="font-semibold text-foreground">问答与对话 (Chat)</span>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[10px] font-mono",
                    effective?.model
                      ? "border-emerald-600/40 text-emerald-700 dark:text-emerald-300 bg-emerald-500/10"
                      : "border-amber-600/40 text-amber-700 dark:text-amber-300 bg-amber-500/10"
                  )}
                >
                  {effective?.model ? "就绪 (Ready)" : "未配置 (Unconfigured)"}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                驱动即时问答、学术导读、摘要提炼与对话草稿润色。
              </p>
              <div className="pt-2 border-t border-border/40 text-[11px] font-mono text-muted-foreground flex justify-between">
                <span>依赖: model ({effective?.model || "未设置"})</span>
                <span>{effective?.api_key_configured ? "Key ✓" : "Key ✗"}</span>
              </div>
            </div>

            {/* 2. Embedding */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 flex flex-col justify-between space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 font-medium text-xs">
                  <Search className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                  <span className="font-semibold text-foreground">语义向量 (Embedding)</span>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[10px] font-mono",
                    effective?.embedding_model
                      ? "border-blue-600/40 text-blue-700 dark:text-blue-300 bg-blue-500/10"
                      : "border-amber-600/40 text-amber-700 dark:text-amber-300 bg-amber-500/10"
                  )}
                >
                  {effective?.embedding_model ? "向量就绪 (Vector Ready)" : "已降级 (Recency 兜底)"}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                资料库向量索引、RAG 语义检索与分层记忆语义召回。未配置时降级为时效性与关键词匹配。
              </p>
              <div className="pt-2 border-t border-border/40 text-[11px] font-mono text-muted-foreground">
                <span>依赖: {effective?.embedding_model || "未指定（自动降级为 recency 兜底）"}</span>
              </div>
            </div>

            {/* 3. Reranker */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 flex flex-col justify-between space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 font-medium text-xs">
                  <ShieldCheck className="h-4 w-4 text-purple-600 dark:text-purple-400" />
                  <span className="font-semibold text-foreground">证据精排 (Reranker)</span>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[10px] font-mono",
                    effective?.reranker_model
                      ? "border-purple-600/40 text-purple-700 dark:text-purple-300 bg-purple-500/10"
                      : "border-slate-500/40 text-slate-600 dark:text-slate-400 bg-slate-500/10"
                  )}
                >
                  {effective?.reranker_model ? "二阶精排启用" : "可选 (向量余弦截断)"}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                对知识库检索与网络搜索候选段落进行交叉精排。未配置时直接使用初始检索相似度分。
              </p>
              <div className="pt-2 border-t border-border/40 text-[11px] font-mono text-muted-foreground">
                <span>依赖: {effective?.reranker_model || "未指定（可选能力）"}</span>
              </div>
            </div>

            {/* 4. Visual Assets */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 flex flex-col justify-between space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 font-medium text-xs">
                  <ImageIcon className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                  <span className="font-semibold text-foreground">视觉图表实证 (Visual Assets)</span>
                </div>
                <Badge
                  variant="outline"
                  className="text-[10px] font-mono border-emerald-600/40 text-emerald-700 dark:text-emerald-300 bg-emerald-500/10"
                >
                  内置就绪 (Built-in)
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                本地沙箱自动提取学术论文高清矢量图与位图插图，并在证据链、综述报告中直接图文对照。
              </p>
              <div className="pt-2 border-t border-border/40 text-[11px] font-mono text-muted-foreground">
                <span>依赖: 本地 PyMuPDF / OCR 解析沙箱</span>
              </div>
            </div>

            {/* 5. Deep Research */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 flex flex-col justify-between space-y-2 md:col-span-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 font-medium text-xs">
                  <Cpu className="h-4 w-4 text-emerald-700 dark:text-emerald-300" />
                  <span className="font-semibold text-foreground">深度研究引擎 (Deep Research)</span>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[10px] font-mono",
                    (effective?.engine_model || effective?.model)
                      ? "border-emerald-600/40 text-emerald-700 dark:text-emerald-300 bg-emerald-500/10"
                      : "border-amber-600/40 text-amber-700 dark:text-amber-300 bg-amber-500/10"
                  )}
                >
                  {(effective?.engine_model || effective?.model) ? "流水线就绪" : "未就绪"}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                自主目标分解、文献与网络证据多阶段求交、动态反思与完整学术综述产出。优先使用专用 Engine 模型，未指定时平滑回退至主 Chat 模型。
              </p>
              <div className="pt-2 border-t border-border/40 text-[11px] font-mono text-muted-foreground flex justify-between">
                <span>有效执行模型: {effective?.engine_model || effective?.model || "未配置"}</span>
                <span>内置预算硬截断与死循环看门狗保护 ✓</span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Memory Studio Section */}
      <Card id="settings-memory-section">
        <CardHeader>
          <div className="flex items-center space-x-2">
            <Bookmark className="h-5 w-5 text-emerald-500" />
            <div>
              <CardTitle className="text-base">分层记忆中心 (Memory Studio)</CardTitle>
              <CardDescription className="text-xs">
                审查并治理在对话与学术研究中沉淀的长期偏好、研究事实与核心假设。
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {memories.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-xs">
              当前尚未沉淀任何长期记忆记录。在对话中点击“沉淀记忆”即可将其持久化收纳。
            </div>
          ) : (
            <div className="space-y-2.5">
              {memories.map((mem) => (
                <div
                  key={mem.memory_id}
                  className="p-3 rounded-lg border border-border/70 bg-muted/20 flex items-center justify-between text-xs"
                >
                  <div className="space-y-1">
                    <div className="flex items-center space-x-2">
                      <span className="font-semibold text-foreground">{mem.key}</span>
                      <Badge variant="outline" className="text-[10px] font-mono">
                        {mem.scope || "global"}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground">{mem.value}</p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="text-rose-500 hover:text-rose-600 hover:bg-rose-500/10"
                    onClick={() => handleDeleteMemory(mem.memory_id)}
                    title="删除此记忆"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          {/* P6-B1：召回预览 —— 为什么召回 */}
          <div className="mt-4 pt-4 border-t border-border/60 space-y-2.5">
            <div className="flex items-center gap-2">
              <Input
                value={recallQuery}
                onChange={(e) => setRecallQuery(e.target.value)}
                placeholder="输入查询，预览将召回哪些记忆及原因…"
                className="h-8 text-xs"
                onKeyDown={(e) => e.key === "Enter" && handleRecallPreview()}
              />
              <Button size="sm" variant="outline" className="h-8 text-xs" disabled={!recallQuery.trim() || recallBusy} onClick={handleRecallPreview}>
                {recallBusy ? "召回中…" : "预览召回"}
              </Button>
            </div>
            {recallResult !== null && (
              <div className="space-y-1.5">
                {recallResult.length === 0 ? (
                  <div className="text-xs text-muted-foreground py-2">无相关记忆，不会强行注入上下文。</div>
                ) : (
                  recallResult.map((record) => (
                    <div key={record.memory_id} className="p-2.5 rounded-md border border-border/60 bg-background/60 text-xs flex items-start justify-between gap-2">
                      <div className="space-y-0.5 min-w-0">
                        <p className="text-foreground">{record.statement}</p>
                        <p className="font-mono text-[11px] text-muted-foreground">
                          {record.memory_id.slice(0, 12)} · {record.scope} · score {record.score}
                        </p>
                      </div>
                      <Badge variant="outline" className="text-[10px] font-mono shrink-0 border-emerald-700/40 text-emerald-700 dark:text-emerald-300">
                        {record.reason}
                      </Badge>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* MCP Gateway Section */}
      <Card id="settings-mcp-section">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <Server className="h-5 w-5 text-emerald-500" />
              <div>
                <CardTitle className="text-base">Model Context Protocol (MCP) 工具网关</CardTitle>
                <CardDescription className="text-xs">
                  双向 MCP 协议网关：支持接入外部 stdio / sse 工具服务，亦可将 Conflux-Weave 本地学术能力对外暴露至 Claude Desktop 或 Cursor。
                </CardDescription>
              </div>
            </div>
            <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setMcpDialogOpen(true)}>
              <Plus className="h-3 w-3 mr-1" />
              接入外部 Server
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Server List */}
          {mcpLoading ? (
            <div className="text-center py-6 text-muted-foreground text-xs font-mono animate-pulse">
              正在加载 MCP 服务列表...
            </div>
          ) : mcpServers.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground text-xs">
              暂未接入外部 MCP Server。点击上方按钮可接入本地 stdio 或远程 sse 工具服务。
            </div>
          ) : (
            <div className="space-y-2.5">
              {mcpServers.map((s) => (
                <div
                  key={s.server_id}
                  className="p-3.5 rounded-lg border border-border/70 bg-muted/20 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs"
                >
                  <div className="space-y-1.5">
                    <div className="flex items-center space-x-2">
                      <span className="font-semibold text-foreground text-sm">{s.name}</span>
                      <Badge variant="outline" className="text-[10px] font-mono">
                        {s.transport}
                      </Badge>
                      <Badge
                        variant="secondary"
                        className={`text-[10px] font-mono ${
                          s.status === "connected"
                            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                            : "bg-amber-500/10 text-amber-600"
                        }`}
                      >
                        {s.status || "connected"}
                      </Badge>
                    </div>
                    <div className="text-[11px] font-mono text-muted-foreground">
                      {s.transport === "stdio" ? (
                        <span>命令: <code>{s.command} {(s.args || []).join(" ")}</code></span>
                      ) : (
                        <span>端点: <code>{s.url}</code></span>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1 pt-1">
                      {(s.tools || []).map((t) => (
                        <span
                          key={t.name}
                          title={t.description}
                          className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[10px] font-mono"
                        >
                          {t.name}
                        </span>
                      ))}
                      {(s.tools || []).length === 0 && (
                        <span className="text-[10px] text-muted-foreground">未同步工具</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center space-x-2 self-end sm:self-center">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs"
                      disabled={syncingServerId === s.server_id}
                      onClick={() => handleSyncMcp(s.server_id)}
                    >
                      <RefreshCw className={`h-3 w-3 mr-1 ${syncingServerId === s.server_id ? "animate-spin" : ""}`} />
                      同步工具
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 text-xs text-rose-500 hover:text-rose-600 hover:bg-rose-500/10"
                      onClick={() => handleDeleteMcp(s.server_id)}
                    >
                      <Trash2 className="h-3 w-3 mr-1" />
                      移除
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Claude Desktop & Cursor Integration Snippet */}
          <div className="p-4 rounded-lg border border-border bg-card space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="text-xs font-semibold text-foreground">对外暴露学术工具配置 (Claude Desktop / Cursor)</h4>
                <p className="text-[11px] text-muted-foreground">复制 JSON 配置至 Claude Desktop 或 Cursor 客户端：</p>
              </div>
              <div className="flex items-center space-x-2">
                <div className="flex rounded-md bg-muted p-0.5 text-[11px] font-mono">
                  <button
                    type="button"
                    onClick={() => setMcpSnippetTab("sse")}
                    className={`px-2 py-0.5 rounded ${mcpSnippetTab === "sse" ? "bg-background text-foreground font-medium shadow-2xs" : "text-muted-foreground"}`}
                  >
                    HTTP / SSE
                  </button>
                  <button
                    type="button"
                    onClick={() => setMcpSnippetTab("stdio")}
                    className={`px-2 py-0.5 rounded ${mcpSnippetTab === "stdio" ? "bg-background text-foreground font-medium shadow-2xs" : "text-muted-foreground"}`}
                  >
                    Stdio 进程
                  </button>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-[11px]"
                  onClick={() => {
                    const origin = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";
                    const sseUrl = origin ? `${origin}/api/v1/mcp/sse` : "/api/v1/mcp/sse";
                    const snippet = mcpSnippetTab === "sse"
                      ? JSON.stringify({ mcpServers: { "conflux-weave": { url: sseUrl } } }, null, 2)
                      : JSON.stringify({ mcpServers: { "conflux-weave": { command: "conflux-weave", args: ["mcp-serve"] } } }, null, 2);
                    navigator.clipboard.writeText(snippet);
                    setMcpCopied(true);
                    setTimeout(() => setMcpCopied(false), 2000);
                  }}
                >
                  {mcpCopied ? <Check className="h-3 w-3 mr-1" /> : <Copy className="h-3 w-3 mr-1" />}
                  {mcpCopied ? "已复制" : "复制"}
                </Button>
              </div>
            </div>
            <pre className="p-3 rounded bg-muted/40 text-[11px] font-mono overflow-x-auto text-foreground/90">
              {(() => {
                const origin = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";
                const sseUrl = origin ? `${origin}/api/v1/mcp/sse` : "/api/v1/mcp/sse";
                return mcpSnippetTab === "sse"
                  ? JSON.stringify({ mcpServers: { "conflux-weave": { url: sseUrl } } }, null, 2)
                  : JSON.stringify({ mcpServers: { "conflux-weave": { command: "conflux-weave", args: ["mcp-serve"] } } }, null, 2);
              })()}
            </pre>
          </div>
        </CardContent>
      </Card>

      {/* Add MCP Server Dialog */}
      <Dialog open={mcpDialogOpen} onOpenChange={setMcpDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold">接入外部 MCP Server</DialogTitle>
            <DialogDescription className="text-xs">
              通过 stdio 命令行进程或远程 SSE 端点将外部工具引入 Conflux-Weave。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleRegisterMcp} className="space-y-3.5 pt-2 text-xs">
            <div className="space-y-1">
              <label className="font-medium text-foreground">Server ID</label>
              <Input
                required
                placeholder="例如: fetch-server 或 filesystem"
                value={newServerId}
                onChange={(e) => setNewServerId(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <label className="font-medium text-foreground">显示名称</label>
              <Input
                required
                placeholder="例如: Fetch 工具服务"
                value={newServerName}
                onChange={(e) => setNewServerName(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <label className="font-medium text-foreground">通信协议类型</label>
              <select
                value={newServerTransport}
                onChange={(e) => setNewServerTransport(e.target.value as "stdio" | "sse")}
                className="w-full h-9 rounded-md border border-input bg-transparent px-3 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="stdio">stdio (本地子进程)</option>
                <option value="sse">sse (远程 HTTP / SSE)</option>
              </select>
            </div>

            {newServerTransport === "stdio" ? (
              <>
                <div className="space-y-1">
                  <label className="font-medium text-foreground">可执行命令 (Command)</label>
                  <Input
                    required
                    placeholder="例如: npx 或 python"
                    value={newServerCommand}
                    onChange={(e) => setNewServerCommand(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <label className="font-medium text-foreground">参数列表 (空格分隔)</label>
                  <Input
                    placeholder="例如: -y @modelcontextprotocol/server-fetch"
                    value={newServerArgs}
                    onChange={(e) => setNewServerArgs(e.target.value)}
                  />
                </div>
              </>
            ) : (
              <div className="space-y-1">
                <label className="font-medium text-foreground">SSE 服务端点 URL</label>
                <Input
                  required
                  type="url"
                  placeholder="例如: /api/v1/mcp/sse 或 localhost:3000/sse"
                  value={newServerUrl}
                  onChange={(e) => setNewServerUrl(e.target.value)}
                />
              </div>
            )}

            {newServerError && (
              <div className="p-2 rounded bg-rose-500/10 text-rose-600 text-xs">
                {newServerError}
              </div>
            )}

            <div className="flex justify-end space-x-2 pt-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => setMcpDialogOpen(false)}>
                取消
              </Button>
              <Button type="submit" size="sm">
                确认接入
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
};
