import React, { useState, useEffect, useRef } from "react";
import {
  Send,
  Bot,
  User,
  Sparkles,
  BookOpen,
  Search,
  BookmarkPlus,
  Plus,
  MessageSquare,
  PanelLeftClose,
  PanelLeft,
  Clock,
} from "lucide-react";
import { marked } from "marked";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { ChatMessage, ConversationSummary } from "@/types/workbench";

const MODE_CONFIG = {
  direct: {
    label: "直接问答",
    desc: "模型即时响应 · 概念解释与常识推理",
    icon: Sparkles,
  },
  rag: {
    label: "知识库问答",
    desc: "基于向量资料库（LanceDB）检索 · 附带文献片段引用",
    icon: BookOpen,
  },
  deep: {
    label: "深度研究引擎",
    desc: "自动化假设规划、证据求交与结构化研究报告",
    icon: Search,
  },
};

export const ChatView: React.FC = () => {
  const [mode, setMode] = useState<"direct" | "rag" | "deep">("direct");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isHistoryOpen, setIsHistoryOpen] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Load conversation list on mount
  const refreshConversations = async (targetConvId?: string) => {
    try {
      setLoadingHistory(true);
      const res = await api.getConversations();
      const items = res.items || [];
      setConversations(items);

      // Match target, or check URL hash, or fallback to first
      const requestedId = targetConvId || (window.location.hash.match(/^#\/chat\/([^?]+)/) || [])[1];
      const found = items.find((it) => it.conversation_id === requestedId) || items[0];

      if (found && !activeConversationId) {
        await selectConversation(found.conversation_id);
      }
    } catch (err) {
      console.error("Failed to load conversations:", err);
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => {
    refreshConversations();
  }, []);

  const selectConversation = async (convId: string) => {
    try {
      setActiveConversationId(convId);
      const detail = await api.getConversationDetail(convId);
      setMessages(detail.messages || []);
      if (detail.active_mode && (detail.active_mode === "direct" || detail.active_mode === "rag" || detail.active_mode === "deep")) {
        setMode(detail.active_mode as any);
      }
      window.history.replaceState(null, "", `#/chat/${encodeURIComponent(convId)}`);
    } catch (err) {
      console.error("Failed to load conversation detail:", err);
    }
  };

  const handleNewConversation = () => {
    setActiveConversationId(null);
    setMessages([]);
    setInput("");
    window.history.replaceState(null, "", "#/chat");
  };

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const q = input.trim();
    if (!q || loading) return;

    const userMsg: ChatMessage = {
      role: "user",
      content: q,
      mode,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await api.sendChat({
        question: q,
        conversation_id: activeConversationId || undefined,
        mode,
      });

      const replyContent = res.answer || res.content || res.text || "已完成分析回答。";
      const assistantMsg: ChatMessage = {
        role: "assistant",
        content: replyContent,
        mode: res.routed_mode || res.mode || mode,
        created_at: new Date().toISOString(),
        memory_candidates: res.memory_candidates,
      };
      setMessages((prev) => [...prev, assistantMsg]);

      if (res.conversation_id) {
        setActiveConversationId(res.conversation_id);
        window.history.replaceState(null, "", `#/chat/${encodeURIComponent(res.conversation_id)}`);
        // Refresh conversations in background
        api.getConversations().then((cRes) => setConversations(cRes.items || [])).catch(() => {});
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `回答发生错误: ${err.message}`,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const renderMarkdown = (content: string) => {
    try {
      return { __html: marked.parse(content, { gfm: true, breaks: true }) as string };
    } catch {
      return { __html: content };
    }
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] w-full overflow-hidden bg-background">
      {/* Left Conversation History Drawer */}
      <aside
        className={cn(
          "shrink-0 border-r border-border/70 bg-card/40 flex flex-col transition-all duration-200 ease-in-out",
          isHistoryOpen ? "w-64 md:w-72" : "w-0 border-r-0"
        )}
      >
        {isHistoryOpen && (
          <div className="flex flex-col h-full overflow-hidden">
            {/* Drawer Header */}
            <div className="p-3 border-b border-border/60 flex items-center justify-between bg-card/60">
              <div className="flex items-center space-x-2">
                <MessageSquare className="h-4 w-4 text-emerald-800 dark:text-emerald-400" />
                <span className="text-sm font-serif-academic font-bold text-foreground">
                  对话历史
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleNewConversation}
                className="h-7 px-2 text-xs font-serif-academic gap-1 border-border/80 hover:border-emerald-800/40"
              >
                <Plus className="h-3 w-3" />
                <span>新建</span>
              </Button>
            </div>

            {/* Conversation Threads List */}
            <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
              {loadingHistory && conversations.length === 0 ? (
                <div className="p-4 space-y-2">
                  <div className="h-4 bg-muted rounded animate-pulse w-3/4" />
                  <div className="h-4 bg-muted rounded animate-pulse w-1/2" />
                  <div className="h-4 bg-muted rounded animate-pulse w-2/3" />
                </div>
              ) : conversations.length === 0 ? (
                <div className="text-center py-12 px-3 text-xs sm:text-sm font-serif-academic text-foreground/70">
                  暂无历史会话记录。<br />提交新问题即可创建对话。
                </div>
              ) : (
                conversations.map((conv) => {
                  const active = conv.conversation_id === activeConversationId;
                  return (
                    <button
                      key={conv.conversation_id}
                      onClick={() => selectConversation(conv.conversation_id)}
                      className={cn(
                        "w-full text-left p-3 rounded-lg text-xs sm:text-sm font-serif-academic transition-all flex flex-col gap-1.5 cursor-pointer",
                        active
                          ? "bg-primary/10 text-primary dark:text-emerald-200 border border-primary/25 font-semibold shadow-xs"
                          : "hover:bg-muted/70 text-foreground/80 hover:text-foreground border border-transparent"
                      )}
                    >
                      <div className="flex items-center justify-between gap-1.5">
                        <span className="truncate font-semibold text-foreground flex-1 text-xs sm:text-sm">
                          {conv.title || "未命名对话"}
                        </span>
                        {conv.message_count > 0 && (
                          <span className="text-xs font-mono text-foreground/75 shrink-0 font-medium">
                            {conv.message_count} 问
                          </span>
                        )}
                      </div>
                      <div className="flex items-center justify-between text-xs text-foreground/70 font-mono">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {conv.updated_at ? new Date(conv.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "近期"}
                        </span>
                        {conv.active_mode && (
                          <Badge variant="outline" className="text-[11px] px-1.5 py-0.5 h-4.5 border-border/70 text-foreground/75 font-mono">
                            {conv.active_mode}
                          </Badge>
                        )}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        )}
      </aside>

      {/* Main Conversation Canvas */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Top Header Mode Bar */}
        <header className="shrink-0 border-b border-border/70 bg-card/60 px-4 sm:px-6 py-2.5 flex items-center justify-between backdrop-blur-sm">
          <div className="flex items-center space-x-2">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
              onClick={() => setIsHistoryOpen(!isHistoryOpen)}
              title={isHistoryOpen ? "收起历史对话" : "展开历史对话"}
            >
              {isHistoryOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeft className="h-4 w-4" />}
            </Button>

            <div className="flex items-center space-x-1.5 bg-muted/60 p-1 rounded-lg">
              {(["direct", "rag", "deep"] as const).map((m) => {
                const Conf = MODE_CONFIG[m];
                const Icon = Conf.icon;
                const active = mode === m;
                return (
                  <button
                    key={m}
                    onClick={() => setMode(m)}
                    className={cn(
                      "flex items-center space-x-1.5 px-3 py-1 rounded-md text-xs font-serif-academic transition",
                      active
                        ? "bg-background text-foreground shadow-xs font-semibold border border-border/60"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <Icon className={cn("h-3.5 w-3.5", active ? "text-emerald-800 dark:text-emerald-400" : "")} />
                    <span>{Conf.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <p className="text-xs font-serif-academic text-muted-foreground hidden sm:block">
            {MODE_CONFIG[mode].desc}
          </p>
        </header>

        {/* Main Conversation Scroll Area */}
        <div className="flex-1 overflow-y-auto w-full flex justify-center py-6 px-4">
          <div className="w-full max-w-3xl space-y-6">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center min-h-[50vh] text-center space-y-4 py-12">
                <div className="h-14 w-14 rounded-2xl bg-emerald-900/10 text-emerald-800 dark:text-emerald-300 flex items-center justify-center shadow-xs">
                  <Sparkles className="h-7 w-7" />
                </div>
                <h3 className="text-xl font-serif-academic font-bold text-foreground">
                  开启学术与工程对话
                </h3>
                <p className="text-xs font-serif-academic text-muted-foreground max-w-md leading-relaxed">
                  当前处于【{MODE_CONFIG[mode].label}】模式。支持直接概念推理、本地资料库证据精准检索、以及多智能体深度综述规划。
                </p>
                <div className="pt-2 flex flex-wrap gap-2 justify-center max-w-lg">
                  {[
                    "介绍多模态向量索引在学术文献中的构建原理",
                    "分析表面码（Surface Code）的最新容错阈值",
                    "检索资料库中关于大模型上下文扩展的论文",
                  ].map((suggestion, i) => (
                    <button
                      key={i}
                      onClick={() => {
                        setInput(suggestion);
                      }}
                      className="text-xs font-serif-academic px-3.5 py-1.5 rounded-full border border-border/80 bg-card hover:bg-muted text-foreground transition shadow-2xs"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((msg, i) => {
                const isUser = msg.role === "user";
                return (
                  <div
                    key={i}
                    className={cn(
                      "flex space-x-3 text-sm animate-in fade-in-50",
                      isUser ? "justify-end" : "justify-start"
                    )}
                  >
                    {!isUser && (
                      <div className="h-8 w-8 rounded-lg bg-emerald-900/10 text-emerald-800 dark:text-emerald-300 flex items-center justify-center shrink-0 mt-0.5">
                        <Bot className="h-4 w-4" />
                      </div>
                    )}
                    <div
                      className={cn(
                        "rounded-2xl px-5 py-4 max-w-[85%] space-y-2 text-sm leading-relaxed shadow-xs",
                        isUser
                          ? "bg-emerald-800 text-white font-sans text-sm sm:text-base"
                          : "border border-border/80 bg-card text-foreground top-bevel font-serif-academic text-base"
                      )}
                    >
                      {isUser ? (
                        <div className="whitespace-pre-wrap">{msg.content}</div>
                      ) : (
                        <div
                          className="prose prose-stone dark:prose-invert max-w-none text-base leading-relaxed font-serif-academic text-foreground"
                          dangerouslySetInnerHTML={renderMarkdown(msg.content)}
                        />
                      )}

                      {/* Memory Candidate Bubble */}
                      {msg.memory_candidates && msg.memory_candidates.length > 0 && (
                        <div className="pt-3 border-t border-border/60 flex flex-col gap-1.5 font-sans">
                          <span className="text-xs text-foreground/80 font-mono flex items-center gap-1 font-semibold">
                            <BookmarkPlus className="h-3.5 w-3.5 text-amber-700 dark:text-amber-400" />
                            识别出长期学术事实记忆:
                          </span>
                          {msg.memory_candidates.map((mem) => (
                            <div
                              key={mem.candidate_id}
                              className="bg-muted/60 p-2.5 rounded-md text-xs sm:text-sm flex items-center justify-between border border-border/50"
                            >
                              <span className="text-foreground font-medium">{mem.key}: {mem.value}</span>
                              <Badge variant="outline" className="text-xs font-mono cursor-pointer hover:bg-primary/10">
                                沉淀入库
                              </Badge>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    {isUser && (
                      <div className="h-8 w-8 rounded-lg bg-muted text-muted-foreground flex items-center justify-center shrink-0 mt-0.5">
                        <User className="h-4 w-4" />
                      </div>
                    )}
                  </div>
                );
              })
            )}
            {loading && (
              <div className="flex space-x-3 text-sm animate-pulse">
                <div className="h-8 w-8 rounded-lg bg-emerald-900/10 text-emerald-800 dark:text-emerald-300 flex items-center justify-center shrink-0">
                  <Bot className="h-4 w-4" />
                </div>
                <div className="rounded-2xl px-5 py-4 bg-card border border-border/80 text-xs text-muted-foreground font-serif-academic">
                  正在深度检索语料与多源证据推理中...
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Bottom Input Composer */}
        <footer className="shrink-0 border-t border-border/70 bg-card/70 px-6 py-3 flex justify-center backdrop-blur-sm">
          <form onSubmit={handleSend} className="w-full max-w-3xl relative flex items-end gap-2">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`输入学术问题（Enter 发送，Shift+Enter 换行）...`}
              className="w-full text-xs sm:text-sm p-3 pr-12 rounded-xl border border-input bg-background/90 focus:outline-none focus:ring-2 focus:ring-emerald-800/40 shadow-xs resize-none"
            />
            <Button
              type="submit"
              size="icon"
              disabled={!input.trim() || loading}
              className="absolute right-2.5 bottom-2.5 h-8 w-8 rounded-lg bg-emerald-800 hover:bg-emerald-900 text-white shadow-xs"
            >
              <Send className="h-4 w-4" />
            </Button>
          </form>
        </footer>
      </div>
    </div>
  );
};
