import React, { useState, useEffect } from "react";
import { Cpu, Bookmark, Trash2, Server, RefreshCw, Plus, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { api } from "@/services/api";
import type { MCPServer } from "@/types/workbench";

export const SettingsView: React.FC = () => {
  const [provider, setProvider] = useState("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [memories, setMemories] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

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

  useEffect(() => {
    api.getConfig().then((cfg) => {
      if (cfg?.provider) {
        setProvider(cfg.provider.name || "openai");
        setBaseUrl(cfg.provider.base_url || "");
        setModel(cfg.provider.model || "");
      }
    }).catch(() => {});

    api.getMemories().then((res) => setMemories(res.items || [])).catch(() => {});
    fetchMcpServers();
  }, []);

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
      await api.updateProviderConfig({
        provider,
        base_url: baseUrl.trim() || undefined,
        api_key: apiKey.trim() || undefined,
        model: model.trim() || undefined,
      });
      setStatusMsg("Provider 配置已成功保存！");
    } catch (e: any) {
      setStatusMsg(`保存失败: ${e.message}`);
    } finally {
      setSaving(false);
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
                placeholder="••••••••••••••••••••••••"
              />
            </div>

            {statusMsg && (
              <div className="p-2.5 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-600 dark:text-emerald-400 font-medium">
                {statusMsg}
              </div>
            )}

            <div className="flex justify-end pt-2">
              <Button type="submit" size="sm" disabled={saving}>
                {saving ? "正在保存..." : "保存配置"}
              </Button>
            </div>
          </form>
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
