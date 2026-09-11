import React, { useState, useEffect } from "react";
import { Cpu, Bookmark, Trash2, Key, Server, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";

export const SettingsView: React.FC = () => {
  const [provider, setProvider] = useState("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [memories, setMemories] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    api.getConfig().then((cfg) => {
      if (cfg?.provider) {
        setProvider(cfg.provider.name || "openai");
        setBaseUrl(cfg.provider.base_url || "");
        setModel(cfg.provider.model || "");
      }
    }).catch(() => {});

    api.getMemories().then((res) => setMemories(res.items || [])).catch(() => {});
  }, []);

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
    </div>
  );
};
