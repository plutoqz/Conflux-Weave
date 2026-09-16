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
  Loader2,
  CheckCircle2,
  ExternalLink,
  Download,
  Pencil,
  Archive,
  Trash2,
  RotateCcw,
  Globe,
  AlertTriangle,
} from "lucide-react";
import { marked } from "marked";
import { renderMarkdownWithMath } from "@/lib/math";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { ChatMessage, ConversationSummary } from "@/types/workbench";

const renderMarkdown = (content: string) => {
  try {
    return { __html: renderMarkdownWithMath(content, { enableCitations: true }) };
  } catch {
    return { __html: content };
  }
};

const handleCitationClick = (e: React.MouseEvent<HTMLDivElement>) => {
  const target = (e.target as HTMLElement).closest(".citation-badge");
  if (target) {
    const citeId = target.getAttribute("data-cite");
    if (citeId) {
      const refEl = document.getElementById(`cite-${citeId}`);
      if (refEl) {
        refEl.scrollIntoView({ behavior: "smooth", block: "center" });
        refEl.classList.add("highlight-flash");
        setTimeout(() => refEl.classList.remove("highlight-flash"), 2200);
      }
    }
  }
};

const DeepResearchMessageBubble: React.FC<{
  message: ChatMessage;
  onReportLoaded: (content: string) => void;
}> = ({ message, onReportLoaded }) => {
  const [runState, setRunState] = useState<string>("running");
  const [stepCount, setStepCount] = useState<number>(0);
  const [currentPhase, setCurrentPhase] = useState<string>("");
  const isPending = message.content.includes("已为您启动深度研究任务");
  const [reportText, setReportText] = useState<string>(!isPending ? message.content : "");
  const [loadingReport, setLoadingReport] = useState<boolean>(false);

  const fetchReport = async (runId: string, detail?: any) => {
    try {
      setLoadingReport(true);
      const d = detail || (await api.getRunDetail(runId));
      const artifactIds = d?.delivery?.artifact_ids || d?.delivery?.artifact_refs || [];
      if (artifactIds.length > 0) {
        const res = await api.getArtifactContent(runId, artifactIds[0]);
        const content = typeof res === "string" ? res : res.content;
        if (content && typeof content === "string") {
          setReportText(content);
          onReportLoaded(content);
          return;
        }
      }
      if (d?.report_content) {
        setReportText(d.report_content);
        onReportLoaded(d.report_content);
      }
    } catch (err) {
      console.error("加载深度研究报告失败:", err);
    } finally {
      setLoadingReport(false);
    }
  };

  const handleTerminal = (st: string, detail: any) => {
    // P6-A3：浏览器通知 + 页面内横幅（离开页面也能看到完成/失败）
    const title = message.content.replace(/^.*?(?=(深度研究|【))/, "").slice(0, 60) || "深度研究任务";
    const finishedOk = st === "complete" || st === "partial";
    try {
      if (typeof Notification !== "undefined") {
        if (Notification.permission === "granted") {
          const n = new Notification(finishedOk ? "深度研究已完成" : `深度研究${st}`, {
            body: finishedOk ? `${title} —— 报告已就绪，点击查看。` : `Run ${message.run_id}，点击进入错误与恢复入口。`,
            tag: message.run_id || undefined,
          });
          n.onclick = () => {
            window.focus();
            window.location.hash = `#/research?run_id=${encodeURIComponent(message.run_id!)}`;
          };
        } else if (Notification.permission === "default") {
          Notification.requestPermission();
        }
      }
    } catch {}
    if (finishedOk) {
      fetchReport(message.run_id!, detail);
    }
  };

  useEffect(() => {
    if (!message.run_id) return;

    let cancelled = false;

    // Immediate initial check on mount
    api.getRunDetail(message.run_id).then((detail) => {
      if (cancelled) return;
      const st = detail.state || detail.status || "running";
      setRunState(st);
      if (detail.progress) {
        if (detail.progress.current_phase) setCurrentPhase(detail.progress.current_phase);
        if (detail.progress.total_steps) setStepCount(detail.progress.total_steps);
      }
      if (st === "complete" || st === "partial") {
        fetchReport(message.run_id!, detail);
      }
    }).catch(() => {});

    if (!isPending) return;

    // P6-A3：主通道 = SSE 事件流（带游标补发与断线重连）；轮询降级为兜底。
    const unsubscribe = api.subscribeRunEvents(message.run_id, {
      onEvent: (event) => {
        if (cancelled) return;
        if (event.state) setRunState(event.state);
        if (event.message) setCurrentPhase(event.message);
      },
      onTerminal: (state) => {
        if (cancelled || !state) return;
        setRunState(state);
        api.getRunDetail(message.run_id!).then((detail) => handleTerminal(state, detail)).catch(() => {});
      },
    });

    // 兜底轮询（SSE 不可用时仍能推进；同轮询内做报告回填）
    const interval = setInterval(async () => {
      try {
        const detail = await api.getRunDetail(message.run_id!);
        if (cancelled) return;
        const st = detail.state || detail.status || "running";
        setRunState(st);
        if (detail.progress) {
          if (detail.progress.current_phase) setCurrentPhase(detail.progress.current_phase);
          if (detail.progress.total_steps) setStepCount(detail.progress.total_steps);
        }

        if (st === "complete" || st === "partial") {
          clearInterval(interval);
          unsubscribe();
          handleTerminal(st, detail);
        } else if (st === "failed" || st === "waiting_for_user" || st === "cancelled" || st === "expired") {
          clearInterval(interval);
          unsubscribe();
          setRunState(st);
          handleTerminal(st, detail);
        }
      } catch (err) {
        console.error("Polling run detail error:", err);
      }
    }, 2500);

    return () => {
      cancelled = true;
      clearInterval(interval);
      unsubscribe();
    };
  }, [message.run_id, isPending]);

  const isCancelled = runState === "cancelled";
  const isWaitingForUser = runState === "waiting_for_user";
  const isFailed = runState === "failed" || runState === "expired";

  return (
    <div className="space-y-3">
      {isCancelled ? (
        <div className="rounded-xl border border-border/80 bg-muted/40 p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2 text-muted-foreground font-semibold text-sm">
              <span>⏹️ 研究任务已主动取消</span>
            </div>
            <Badge variant="outline" className="text-xs font-mono text-muted-foreground">
              已取消
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground leading-relaxed font-sans">
            用户已主动终止此深度研究流水线。未生成最终研究报告。
          </p>
          <div className="flex items-center justify-between pt-1 border-t border-border/40 text-xs">
            <span className="font-mono text-muted-foreground">Run ID: {message.run_id}</span>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs text-muted-foreground hover:text-foreground gap-1 px-2"
              onClick={() => {
                window.location.hash = `#/research?run_id=${encodeURIComponent(message.run_id!)}`;
              }}
            >
              <span>在研究中心查看记录</span>
              <ExternalLink className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ) : isWaitingForUser ? (
        <div className="rounded-xl border border-amber-600/30 bg-amber-50/50 dark:bg-amber-950/20 p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2 text-amber-800 dark:text-amber-300 font-semibold text-sm">
              <span>⏸️ 研究任务暂停：待人工确认</span>
            </div>
            <Badge variant="outline" className="text-xs font-mono border-amber-600/40 text-amber-800 dark:text-amber-300">
              待用户决策
            </Badge>
          </div>
          <p className="text-xs text-foreground/80 leading-relaxed font-sans">
            研究流水线在推进到第 {stepCount} 阶段时暂停，遇到了需要确认决策的外部调用或恢复检查点。
          </p>
          <div className="flex items-center justify-between pt-1 border-t border-amber-600/20 text-xs">
            <span className="font-mono text-muted-foreground">Run ID: {message.run_id}</span>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs text-amber-900 dark:text-amber-200 hover:bg-amber-600/10 gap-1 px-2"
              onClick={() => {
                window.location.hash = `#/research?run_id=${encodeURIComponent(message.run_id!)}`;
              }}
            >
              <span>在研究中心查看并恢复</span>
              <ExternalLink className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ) : isFailed ? (
        <div className="rounded-xl border border-rose-600/30 bg-rose-50/50 dark:bg-rose-950/20 p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2 text-rose-800 dark:text-rose-300 font-semibold text-sm">
              <span>⚠️ 深度研究任务执行异常</span>
            </div>
            <Badge variant="outline" className="text-xs font-mono border-rose-600/40 text-rose-800 dark:text-rose-300">
              {runState}
            </Badge>
          </div>
          <p className="text-xs text-foreground/80 leading-relaxed font-sans">
            研究流水线在推进到第 {stepCount} 阶段时发生错误。您可以前往研究中心查看具体阶段错误日志或重试该任务。
          </p>
          <div className="flex items-center justify-between pt-1 border-t border-rose-600/20 text-xs">
            <span className="font-mono text-muted-foreground">Run ID: {message.run_id}</span>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs text-rose-900 dark:text-rose-200 hover:bg-rose-600/10 gap-1 px-2"
              onClick={() => {
                window.location.hash = `#/research?run_id=${encodeURIComponent(message.run_id!)}`;
              }}
            >
              <span>在研究中心查看错误与重试</span>
              <ExternalLink className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ) : runState === "complete" || runState === "partial" ? (
        <div className="space-y-3">
          <div className="rounded-xl border border-emerald-600/30 bg-emerald-50/60 dark:bg-emerald-950/20 p-4 space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2 text-emerald-800 dark:text-emerald-300 font-semibold text-sm">
                <CheckCircle2 className="h-4 w-4" />
                <span>深度研究已完成（{runState}）</span>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs text-emerald-900 dark:text-emerald-200 hover:bg-emerald-600/10 gap-1 px-2"
                onClick={() => {
                  window.location.hash = `#/research?run_id=${encodeURIComponent(message.run_id!)}`;
                }}
              >
                <span>在研究中心查看完整证据链</span>
                <ExternalLink className="h-3 w-3" />
              </Button>
            </div>
            <div className="flex items-center justify-between text-xs text-muted-foreground pt-1 border-t border-emerald-600/20">
              <span className="font-mono">Run ID: {message.run_id}</span>
              <span className="text-emerald-700 dark:text-emerald-300">报告已在下方同步渲染</span>
            </div>
          </div>

          {/* Inline Report Rendering */}
          {reportText ? (
            <div className="pt-2" onClick={handleCitationClick}>
              <div
                className="prose prose-stone dark:prose-invert max-w-none text-base leading-relaxed font-serif-academic text-foreground [&_.katex-display]:my-3 [&_.katex-display]:overflow-x-auto [&_.katex-display]:text-center"
                dangerouslySetInnerHTML={renderMarkdown(reportText)}
              />
            </div>
          ) : loadingReport ? (
            <div className="flex items-center space-x-2 p-3 text-xs text-muted-foreground font-sans">
              <Loader2 className="h-4 w-4 animate-spin text-emerald-600" />
              <span>正在获取深度研究综合报告与证据链...</span>
            </div>
          ) : null}
        </div>
      ) : isPending ? (
        <div className="rounded-xl border border-amber-600/30 bg-amber-50/50 dark:bg-amber-950/20 p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2 text-amber-800 dark:text-amber-300 font-semibold text-sm">
              <Loader2 className="h-4 w-4 animate-spin text-amber-600" />
              <span>深度研究引擎持续推理中...</span>
            </div>
            <Badge variant="outline" className="text-xs font-mono border-amber-600/40 text-amber-800 dark:text-amber-300">
              {runState}
            </Badge>
          </div>
          <p className="text-xs text-foreground/80 leading-relaxed font-sans">
            正在执行多智能体计划分解、多源文献检索与闭环证据核验。已推进 {stepCount} 项流水线阶段，结果将自动同步刷新到此处。
            {currentPhase && <span className="block mt-1 font-mono text-amber-700 dark:text-amber-300">当前阶段：{currentPhase}</span>}
          </p>
          <div className="flex items-center justify-between pt-1 border-t border-amber-600/20 text-xs">
            <span className="font-mono text-muted-foreground">Run ID: {message.run_id}</span>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs text-amber-900 dark:text-amber-200 hover:bg-amber-600/10 gap-1 px-2"
              onClick={() => {
                window.location.hash = `#/research?run_id=${encodeURIComponent(message.run_id!)}`;
              }}
            >
              <span>在研究中心查看</span>
              <ExternalLink className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <div className="flex items-center justify-between pb-2 mb-3 border-b border-border/60 text-xs font-mono text-muted-foreground">
            <span className="flex items-center gap-1.5 text-emerald-800 dark:text-emerald-400 font-semibold">
              <CheckCircle2 className="h-3.5 w-3.5" />
              深度学术综述已就绪
            </span>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 text-xs text-emerald-800 dark:text-emerald-300 hover:bg-emerald-900/10 gap-1 px-2"
              onClick={() => {
                window.location.hash = `#/research?run_id=${encodeURIComponent(message.run_id!)}`;
              }}
            >
              <span>查看完整证据链</span>
              <ExternalLink className="h-3 w-3" />
            </Button>
          </div>
          <div
            className="prose prose-stone dark:prose-invert max-w-none text-base leading-relaxed font-serif-academic text-foreground"
            dangerouslySetInnerHTML={renderMarkdown(message.content)}
          />
        </div>
      )}
    </div>
  );
};

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
  const [exportingAnswer, setExportingAnswer] = useState<string>("");
  const [convFilter, setConvFilter] = useState<"active" | "archived" | "deleted">("active");
  const [webSearch, setWebSearch] = useState<boolean>(false);
  const [thinkingDepth, setThinkingDepth] = useState<"quick" | "deep" | "rigorous">("deep");
  const [lightboxImage, setLightboxImage] = useState<{ url: string; caption?: string } | null>(null);
  const [candidateActions, setCandidateActions] = useState<Record<string, "approved" | "rejected" | "loading">>(() => {
    try {
      const saved = localStorage.getItem("cw_candidate_actions");
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  });

  const handleCandidateAction = async (candidateId: string, action: "approve" | "reject") => {
    setCandidateActions((prev) => ({ ...prev, [candidateId]: "loading" }));
    try {
      await api.actOnMemoryCandidate(candidateId, action);
      setCandidateActions((prev) => {
        const next = { ...prev, [candidateId]: action === "approve" ? ("approved" as const) : ("rejected" as const) };
        try {
          localStorage.setItem("cw_candidate_actions", JSON.stringify(next));
        } catch {}
        return next;
      });
    } catch {
      setCandidateActions((prev) => {
        const next = { ...prev };
        delete next[candidateId];
        return next;
      });
    }
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && lightboxImage) {
        setLightboxImage(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [lightboxImage]);

  const handleRenameConversation = async (convId: string, current: string) => {
    const title = window.prompt("重命名对话", current);
    if (!title || !title.trim()) return;
    try {
      await api.renameConversation(convId, title.trim());
      refreshConversations();
    } catch (err) {
      alert(`重命名失败: ${err instanceof Error ? err.message : err}`);
    }
  };
  const handleArchiveConversation = async (convId: string) => {
    try {
      await api.setConversationLifecycle(convId, "archive");
      refreshConversations();
    } catch (err) {
      alert(`归档失败: ${err instanceof Error ? err.message : err}`);
    }
  };
  const handleDeleteConversation = async (convId: string) => {
    if (!window.confirm("确定移入回收站？消息与关联研究仍保留，可随时恢复。")) return;
    try {
      await api.deleteConversation(convId);
      refreshConversations();
    } catch (err) {
      alert(`删除失败: ${err instanceof Error ? err.message : err}`);
    }
  };
  const handleRestoreConversation = async (convId: string) => {
    try {
      await api.restoreConversation(convId);
      refreshConversations();
    } catch (err) {
      alert(`恢复失败: ${err instanceof Error ? err.message : err}`);
    }
  };

  const handleExportAnswer = async (messageId: string, format: "markdown" | "json") => {
    if (exportingAnswer) return;
    setExportingAnswer(messageId);
    try {
      await api.exportChatAnswer(messageId, format);
    } catch (err) {
      console.error("导出回答失败:", err);
    } finally {
      setExportingAnswer("");
    }
  };

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const getRequestedConvId = () => {
    const hash = window.location.hash;
    const pathMatch = (hash.match(/^#\/chat\/([^?]+)/) || [])[1];
    if (pathMatch) return decodeURIComponent(pathMatch);
    const queryPart = hash.split("?")[1] || "";
    const params = new URLSearchParams(queryPart);
    return params.get("conversation_id") || params.get("conv_id") || null;
  };

  const loadDraft = (convId: string | null) => {
    try {
      const draft = localStorage.getItem("cw_draft_" + (convId || "new")) || "";
      setInput(draft);
    } catch {
      setInput("");
    }
  };

  const handleInputChange = (val: string) => {
    setInput(val);
    try {
      if (val) {
        localStorage.setItem("cw_draft_" + (activeConversationId || "new"), val);
      } else {
        localStorage.removeItem("cw_draft_" + (activeConversationId || "new"));
      }
    } catch {}
  };

  // Load conversation list on mount
  const refreshConversations = async (targetConvId?: string, status = convFilter) => {
    try {
      setLoadingHistory(true);
      const res = await api.getConversations(status);
      const items = res.items || [];
      setConversations(items);

      // Match target, or check URL hash, or fallback to first
      const requestedId = targetConvId || getRequestedConvId();
      const found = items.find((it) => it.conversation_id === requestedId) || items[0];

      if (found && (!activeConversationId || activeConversationId !== found.conversation_id)) {
        await selectConversation(found.conversation_id);
      } else if (!items.length) {
        loadDraft(null);
      }
    } catch (err) {
      console.error("Failed to load conversations:", err);
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => {
    refreshConversations();

    // Auto-refresh conversation list silently in background and on window focus
    const silentRefresh = () => {
      api.getConversations(convFilter).then((res) => {
        if (res.items) setConversations(res.items);
      }).catch(() => {});
    };

    const interval = setInterval(silentRefresh, 4000);
    const handleFocus = () => {
      if (document.visibilityState === "visible") silentRefresh();
    };
    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleFocus);

    return () => {
      clearInterval(interval);
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleFocus);
    };
  }, [convFilter]);

  useEffect(() => {
    const handleHashChange = () => {
      const target = getRequestedConvId();
      if (target && target !== activeConversationId) {
        selectConversation(target);
      }
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [activeConversationId]);

  const selectConversation = async (convId: string) => {
    try {
      setActiveConversationId(convId);
      loadDraft(convId);
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
    loadDraft(null);
    window.history.replaceState(null, "", "#/chat");
  };

  const handleSend = async (e?: React.FormEvent, retryPrompt?: string) => {
    if (e) e.preventDefault();
    const q = (retryPrompt || input).trim();
    if (!q || loading) return;

    const userMsg: ChatMessage = {
      role: "user",
      content: q,
      mode,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    try {
      localStorage.removeItem("cw_draft_" + (activeConversationId || "new"));
    } catch {}
    setLoading(true);

    try {
      const res = await api.sendChat({
        question: q,
        conversation_id: activeConversationId || undefined,
        mode,
        web_search: webSearch,
        thinking_depth: thinkingDepth,
      });

      const replyContent = res.answer || res.content || res.text || "已完成分析回答。";
      const assistantMsg: ChatMessage = {
        message_id: res.message_id || res.id,
        role: "assistant",
        content: replyContent,
        mode: res.routed_mode || res.mode || mode,
        created_at: new Date().toISOString(),
        memory_candidates: res.memory_candidates,
        image_assets: res.image_assets,
        run_id: res.run_id,
        citations: res.citations || [],
        verification: res.verification || "model-knowledge",
      };
      setMessages((prev) => [...prev, assistantMsg]);

      if (res.conversation_id) {
        setActiveConversationId(res.conversation_id);
        window.history.replaceState(null, "", `#/chat/${encodeURIComponent(res.conversation_id)}`);
        // Refresh conversations in background
        api.getConversations().then((cRes) => setConversations(cRes.items || [])).catch(() => {});
      }
    } catch (err: any) {
      setInput(q);
      try {
        localStorage.setItem("cw_draft_" + (activeConversationId || "new"), q);
      } catch {}
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

  const updateMessageContent = (index: number, newContent: string) => {
    setMessages((prev) =>
      prev.map((m, idx) => (idx === index ? { ...m, content: newContent } : m))
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
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

            {/* P6-A2 生命周期筛选 */}
            <div className="px-3 py-2 border-b border-border/60 flex items-center gap-1.5">
              {([
                ["active", "进行中"],
                ["archived", "已归档"],
                ["deleted", "回收站"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => {
                    setConvFilter(value);
                    refreshConversations(undefined, value);
                  }}
                  className={cn(
                    "text-xs px-2 py-1 rounded-md font-serif-academic transition cursor-pointer border",
                    convFilter === value
                      ? "bg-primary/10 text-primary dark:text-emerald-200 border-primary/30 font-semibold"
                      : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/60"
                  )}
                >
                  {label}
                </button>
              ))}
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
                  {convFilter === "active" ? (<>暂无历史会话记录。<br />提交新问题即可创建对话。</>) : convFilter === "archived" ? "暂无已归档对话。" : "回收站为空。"}
                </div>
              ) : (
                conversations.map((conv) => {
                  const active = conv.conversation_id === activeConversationId;
                  return (
                    <button
                      key={conv.conversation_id}
                      onClick={() => selectConversation(conv.conversation_id)}
                      className={cn(
                        "group/conv w-full text-left p-3 rounded-lg text-xs sm:text-sm font-serif-academic transition-all flex flex-col gap-1.5 cursor-pointer",
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
                      <div
                        className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground opacity-0 group-hover/conv:opacity-100 transition-opacity"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {convFilter === "active" ? (
                          <>
                            <button type="button" title="重命名" className="hover:text-foreground cursor-pointer bg-transparent border-none p-0" onClick={() => handleRenameConversation(conv.conversation_id, conv.title)}>
                              <Pencil className="h-3 w-3" />
                            </button>
                            <button type="button" title="归档" className="hover:text-foreground cursor-pointer bg-transparent border-none p-0" onClick={() => handleArchiveConversation(conv.conversation_id)}>
                              <Archive className="h-3 w-3" />
                            </button>
                            <button type="button" title="移入回收站" className="hover:text-red-600 cursor-pointer bg-transparent border-none p-0" onClick={() => handleDeleteConversation(conv.conversation_id)}>
                              <Trash2 className="h-3 w-3" />
                            </button>
                          </>
                        ) : (
                          <button type="button" title="恢复到对话列表" className="hover:text-emerald-700 cursor-pointer bg-transparent border-none p-0 flex items-center gap-1" onClick={() => handleRestoreConversation(conv.conversation_id)}>
                            <RotateCcw className="h-3 w-3" />
                            <span>恢复</span>
                          </button>
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
        <div
          className="flex-1 overflow-y-auto w-full flex justify-center py-6 px-4"
          onClick={(e) => {
            const target = e.target as HTMLElement;
            if (target.tagName === "IMG") {
              const src = target.getAttribute("src");
              if (src) {
                e.stopPropagation();
                setLightboxImage({
                  url: src,
                  caption: target.getAttribute("alt") || "论文高清图表实证",
                });
              }
            }
          }}
        >
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
                      ) : msg.run_id ? (
                        <DeepResearchMessageBubble
                          message={msg}
                          onReportLoaded={(newContent) => updateMessageContent(i, newContent)}
                        />
                      ) : (
                        <div onClick={handleCitationClick}>
                          {msg.content.startsWith("回答发生错误:") ? (
                            <div className="flex flex-col gap-2">
                              <div className="text-destructive flex items-center gap-1.5 text-xs font-sans">
                                <AlertTriangle className="h-4 w-4 shrink-0" />
                                <span>{msg.content}</span>
                              </div>
                              {input && (
                                <button
                                  type="button"
                                  onClick={() => handleSend(undefined, input)}
                                  className="self-start text-xs font-sans px-2.5 py-1 rounded bg-destructive/10 text-destructive hover:bg-destructive/20 transition cursor-pointer"
                                >
                                  重试发送
                                </button>
                              )}
                            </div>
                          ) : (
                            <div
                              className="prose prose-stone dark:prose-invert max-w-none text-base leading-relaxed font-serif-academic text-foreground [&_.katex-display]:my-3 [&_.katex-display]:overflow-x-auto [&_.katex-display]:text-center"
                              dangerouslySetInnerHTML={renderMarkdown(msg.content)}
                            />
                          )}
                          {msg.message_id && (
                            <div className="pt-2 mt-3 border-t border-border/40 flex items-center gap-2.5 text-xs font-mono text-muted-foreground">
                              <span className="flex items-center gap-1">
                                <Download className="h-3 w-3" />
                                导出
                              </span>
                              {(["markdown", "json"] as const).map((format) => (
                                <button
                                  key={format}
                                  type="button"
                                  disabled={exportingAnswer === msg.message_id}
                                  onClick={() => handleExportAnswer(msg.message_id!, format)}
                                  className="hover:text-foreground hover:underline bg-transparent border-none p-0 cursor-pointer"
                                >
                                  {exportingAnswer === msg.message_id ? "导出中…" : format.toUpperCase()}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Memory Candidate Bubble */}
                      {msg.memory_candidates && msg.memory_candidates.length > 0 && (
                        <div className="pt-3 border-t border-border/60 flex flex-col gap-1.5 font-sans">
                          <span className="text-xs text-foreground/80 font-mono flex items-center gap-1 font-semibold">
                            <BookmarkPlus className="h-3.5 w-3.5 text-amber-700 dark:text-amber-400" />
                            识别出长期学术事实记忆:
                          </span>
                          {msg.memory_candidates.map((mem) => {
                            const displayText = mem.statement || (mem.key ? `${mem.key}: ${mem.value}` : "学术事实记忆");
                            const actionState = candidateActions[mem.candidate_id] || (mem.status === "approved" ? "approved" : mem.status === "rejected" ? "rejected" : undefined);
                            return (
                              <div
                                key={mem.candidate_id}
                                className="bg-muted/60 p-2.5 rounded-md text-xs sm:text-sm flex items-center justify-between border border-border/50 gap-2"
                              >
                                <span className="text-foreground font-medium">{displayText}</span>
                                {actionState === "approved" ? (
                                  <Badge variant="outline" className="text-xs font-mono text-emerald-600 border-emerald-500/40 bg-emerald-50/50 dark:bg-emerald-950/20 shrink-0">
                                    已沉淀入库
                                  </Badge>
                                ) : actionState === "rejected" ? (
                                  <Badge variant="outline" className="text-xs font-mono text-muted-foreground border-border bg-muted/40 shrink-0">
                                    已忽略
                                  </Badge>
                                ) : (
                                  <div className="flex items-center gap-1.5 shrink-0">
                                    <Badge
                                      variant="outline"
                                      className="text-xs font-mono cursor-pointer hover:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30"
                                      onClick={() => handleCandidateAction(mem.candidate_id, "approve")}
                                    >
                                      {actionState === "loading" ? "保存中…" : "沉淀入库"}
                                    </Badge>
                                    <Badge
                                      variant="outline"
                                      className="text-xs font-mono cursor-pointer hover:bg-destructive/10 text-muted-foreground hover:text-destructive border-border"
                                      onClick={() => handleCandidateAction(mem.candidate_id, "reject")}
                                    >
                                      忽略
                                    </Badge>
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}

                      {/* Image Assets Gallery (P2.3 / P9) */}
                      {msg.image_assets && msg.image_assets.length > 0 && (
                        <div className="pt-3 border-t border-border/60 flex flex-col gap-2 font-sans">
                          <span className="text-xs text-foreground/80 font-mono flex items-center gap-1.5 font-semibold">
                            <Sparkles className="h-3.5 w-3.5 text-emerald-800 dark:text-emerald-400" />
                            检索命中的多模态文献图表 ({msg.image_assets.length} 张):
                          </span>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                            {msg.image_assets.map((img, imgIdx) => (
                              <div
                                key={imgIdx}
                                onClick={() => setLightboxImage({ url: img.url, caption: img.caption })}
                                className="group relative border border-border/70 rounded-xl overflow-hidden bg-muted/40 hover:border-emerald-800/60 hover:shadow-xs transition cursor-pointer"
                              >
                                <div className="aspect-video w-full overflow-hidden bg-black/5 dark:bg-white/5 flex items-center justify-center">
                                  <img
                                    src={img.url}
                                    alt={img.caption}
                                    className="max-h-full max-w-full object-contain group-hover:scale-105 transition duration-200"
                                    loading="lazy"
                                  />
                                </div>
                                <div className="p-2 text-xs">
                                  <div className="flex items-center justify-between text-[11px] text-muted-foreground font-mono mb-1">
                                    <span>{img.page ? `第 ${img.page} 页` : "插图"}</span>
                                    <span className="truncate max-w-[120px]">{img.asset_id}</span>
                                  </div>
                                  <p className="line-clamp-2 text-foreground/90 font-serif-academic text-xs leading-snug">
                                    {img.caption}
                                  </p>
                                </div>
                              </div>
                            ))}
                          </div>
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
        <footer className="shrink-0 border-t border-border/70 bg-card/70 px-4 py-3 flex justify-center backdrop-blur-sm">
          <form onSubmit={handleSend} className="w-full max-w-3xl flex flex-col rounded-2xl border border-border/80 bg-background/95 p-3 shadow-xs focus-within:ring-2 focus-within:ring-emerald-800/30 focus-within:border-emerald-800/60 transition-all">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`输入学术问题（Enter 发送，Shift+Enter 换行）...`}
              className="w-full text-xs sm:text-sm p-1 border-0 bg-transparent focus:outline-none focus:ring-0 resize-none min-h-[48px] max-h-[140px] leading-relaxed text-foreground placeholder:text-muted-foreground"
            />
            {/* Toolbar row with Web Search, Thinking Depth, and Send button perfectly aligned */}
            <div className="flex items-center justify-between pt-2 mt-1 border-t border-border/40 gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                {/* 联网搜索开关 */}
                <button
                  type="button"
                  onClick={() => setWebSearch((prev) => !prev)}
                  className={cn(
                    "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-sans border transition cursor-pointer select-none",
                    webSearch
                      ? "bg-blue-50 text-blue-700 border-blue-300 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800 font-semibold shadow-2xs"
                      : "border-border/70 text-muted-foreground hover:text-foreground hover:bg-muted/60"
                  )}
                  title="开启后将通过实时搜索引擎检索最新权威资讯并注入上下文"
                >
                  <Globe className={cn("h-3.5 w-3.5", webSearch ? "text-blue-600 dark:text-blue-400" : "text-muted-foreground")} />
                  <span>联网搜索</span>
                  <span className={cn("text-[10px] px-1 py-0.2 rounded font-mono", webSearch ? "bg-blue-600 text-white" : "bg-muted text-muted-foreground")}>
                    {webSearch ? "ON" : "OFF"}
                  </span>
                </button>

                {/* 思考深度控制 */}
                <div className="flex items-center rounded-md border border-border/70 p-0.5 bg-muted/40 text-xs">
                  <span className="text-[11px] text-muted-foreground px-1.5 font-serif-academic select-none">思考深度:</span>
                  {([
                    { id: "quick", label: "⚡ 快速", desc: "紧凑响应 · 降低延迟" },
                    { id: "deep", label: "🧠 深度", desc: "详尽推演 · 严谨论证" },
                    { id: "rigorous", label: "🔬 极致研判", desc: "闭环交叉反思 · 证据求交" },
                  ] as const).map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setThinkingDepth(t.id)}
                      title={t.desc}
                      className={cn(
                        "px-2 py-0.5 rounded text-xs transition cursor-pointer",
                        thinkingDepth === t.id
                          ? "bg-background text-foreground font-semibold shadow-2xs border border-border/40"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* 发送按钮 */}
              <Button
                type="submit"
                size="sm"
                disabled={!input.trim() || loading}
                className="h-8 px-3.5 rounded-lg bg-emerald-800 hover:bg-emerald-900 text-white flex items-center gap-1.5 shadow-xs font-medium cursor-pointer shrink-0"
              >
                <span>发送</span>
                <Send className="h-3.5 w-3.5" />
              </Button>
            </div>
          </form>
        </footer>
      </div>

      {/* Lightbox Modal for Image Assets */}
      {lightboxImage && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in-50"
          onClick={() => setLightboxImage(null)}
        >
          <div
            className="relative max-w-4xl max-h-[90vh] bg-card border border-border rounded-2xl overflow-hidden shadow-2xl p-4 flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between pb-2 border-b border-border/60">
              <p className="text-sm font-serif-academic font-medium text-foreground truncate pr-4">
                {lightboxImage.caption || "论文高清图表实证"}
              </p>
              <button
                onClick={() => setLightboxImage(null)}
                className="text-muted-foreground hover:text-foreground text-xs font-mono px-2 py-1 rounded bg-muted/60"
              >
                关闭 (ESC)
              </button>
            </div>
            <div className="flex-1 overflow-auto flex items-center justify-center p-2 min-h-[300px]">
              <img
                src={lightboxImage.url}
                alt={lightboxImage.caption || "放大图表"}
                className="max-h-[75vh] max-w-full object-contain rounded-lg"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
