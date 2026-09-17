import React, { useState, useEffect, useMemo } from "react";
import {
  Bookmark,
  Plus,
  Search,
  BookOpen,
  FileText,
  FlaskConical,
  FolderGit2,
  MessageSquare,
  ExternalLink,
  Trash2,
  Edit3,
  CheckCircle2,
  Clock,
  Tag,
  Compass,
  Unlink,
  X,
  AlertCircle,
  HelpCircle,
  Sparkles,
} from "lucide-react";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { api } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn, formatTimeAgo } from "@/lib/utils";
import type {
  TopicRecord,
  TopicDetail,
  TopicLinkPayload,
  LibraryDocument,
  RunSummary,
  ProjectSummary,
  ConversationSummary,
} from "@/types/workbench";

export const TopicsView: React.FC = () => {
  const {
    topics,
    setTopics,
    activeTopicId,
    setActiveTopicId,
    setSection,
    setActiveRunId,
    openNoteStudio,
  } = useWorkbenchStore();

  const [search, setSearch] = useState("");
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [activeDetail, setActiveDetail] = useState<TopicDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Dialogs
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [isPickerOpen, setIsPickerOpen] = useState(false);
  const [pickerTab, setPickerTab] = useState<"document" | "note" | "run" | "project" | "conversation">("document");

  // Form State
  const [formName, setFormName] = useState("");
  const [formObjective, setFormObjective] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formTags, setFormTags] = useState("");

  // Available objects for picker
  const [availableDocs, setAvailableDocs] = useState<LibraryDocument[]>([]);
  const [availableRuns, setAvailableRuns] = useState<RunSummary[]>([]);
  const [availableProjects, setAvailableProjects] = useState<ProjectSummary[]>([]);
  const [availableConvs, setAvailableConvs] = useState<ConversationSummary[]>([]);
  const [availableNotes, setAvailableNotes] = useState<Array<{ note_id: string; title: string; document_id: string }>>([]);

  // Fetch topics list
  const fetchTopics = async () => {
    try {
      const res = await api.getTopics();
      setTopics(res.items || []);
      if (!activeTopicId && res.items && res.items.length > 0) {
        setActiveTopicId(res.items[0].topic_id);
      }
    } catch (err: any) {
      setErrorMsg(err.message || "获取专题列表失败");
    }
  };

  useEffect(() => {
    fetchTopics();
  }, []);

  // Fetch active topic detail
  useEffect(() => {
    if (!activeTopicId) {
      setActiveDetail(null);
      return;
    }
    setIsLoading(true);
    api.getTopic(activeTopicId)
      .then((detail) => {
        setActiveDetail(detail);
        setIsLoading(false);
      })
      .catch((err) => {
        setErrorMsg(err.message || "加载专题详情失败");
        setIsLoading(false);
      });
  }, [activeTopicId]);

  // Load pool of available assets when picker opens
  useEffect(() => {
    if (!isPickerOpen) return;
    api.getDocuments("active").then((r) => setAvailableDocs(r.items || [])).catch(() => {});
    api.getRuns().then((r) => setAvailableRuns(r.items || [])).catch(() => {});
    api.getProjects().then((r) => setAvailableProjects(r || [])).catch(() => {});
    api.getConversations().then((r) => setAvailableConvs(r.items || [])).catch(() => {});
    // Extract notes from docs
    api.getDocuments("active").then((r) => {
      const notes = (r.items || [])
        .filter((d: any) => Boolean(d.document_id))
        .map((d: any) => ({
          note_id: (d.note_id as string) || `note-${d.document_id}`,
          title: d.title || d.document_id,
          document_id: d.document_id,
        }));
      setAvailableNotes(notes);
    }).catch(() => {});
  }, [isPickerOpen]);

  // All tags
  const allTags = useMemo(() => {
    const s = new Set<string>();
    topics.forEach((t) => (t.tags || []).forEach((tag) => s.add(tag)));
    return Array.from(s);
  }, [topics]);

  // Filtered topics
  const filteredTopics = useMemo(() => {
    return topics.filter((t) => {
      if (selectedTag && !(t.tags || []).includes(selectedTag)) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        const matchName = t.name.toLowerCase().includes(q);
        const matchObj = (t.objective || "").toLowerCase().includes(q);
        const matchTag = (t.tags || []).some((tg) => tg.toLowerCase().includes(q));
        return matchName || matchObj || matchTag;
      }
      return true;
    });
  }, [topics, search, selectedTag]);

  // Handle create topic
  const handleCreateTopic = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) return;
    try {
      const tags = formTags.split(/[,，\s]+/).map((t) => t.trim()).filter(Boolean);
      const res = await api.createTopic({
        name: formName.trim(),
        objective: formObjective.trim(),
        description: formDesc.trim(),
        tags,
      });
      setIsCreateOpen(false);
      setFormName("");
      setFormObjective("");
      setFormDesc("");
      setFormTags("");
      await fetchTopics();
      setActiveTopicId(res.topic_id);
    } catch (err: any) {
      alert("创建专题失败: " + (err.message || String(err)));
    }
  };

  // Handle edit topic
  const handleEditTopic = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeTopicId || !formName.trim()) return;
    try {
      const tags = formTags.split(/[,，\s]+/).map((t) => t.trim()).filter(Boolean);
      await api.updateTopic(activeTopicId, {
        name: formName.trim(),
        objective: formObjective.trim(),
        description: formDesc.trim(),
        tags,
      });
      setIsEditOpen(false);
      await fetchTopics();
      const updated = await api.getTopic(activeTopicId);
      setActiveDetail(updated);
    } catch (err: any) {
      alert("更新专题失败: " + (err.message || String(err)));
    }
  };

  // Handle delete topic
  const handleDeleteTopic = async () => {
    if (!activeTopicId || !activeDetail) return;
    const ok = window.confirm(
      `确定解除并删除研究专题《${activeDetail.name}》吗？\n\n【安全提示】：删除专题仅解除聚合映射，绝对不会删除任何底层文献、笔记、报告、代码项目或对话！`
    );
    if (!ok) return;
    try {
      await api.deleteTopic(activeTopicId);
      setActiveTopicId(null);
      await fetchTopics();
    } catch (err: any) {
      alert("删除专题失败: " + (err.message || String(err)));
    }
  };

  // Handle link / unlink
  const handleLinkObject = async (object_type: TopicLinkPayload["object_type"], object_id: string, action: "link" | "unlink") => {
    if (!activeTopicId) return;
    try {
      const updated = await api.linkTopicObject(activeTopicId, { object_type, object_id, action });
      setActiveDetail(updated);
      fetchTopics();
    } catch (err: any) {
      alert("操作失败: " + (err.message || String(err)));
    }
  };

  // Resume last work location
  const handleResumeLocation = () => {
    if (!activeDetail?.recent_location) return;
    const { section, object_id } = activeDetail.recent_location;
    if (section === "research") {
      if (object_id) setActiveRunId(object_id);
      setSection("research");
    } else if (section === "library") {
      if (object_id) openNoteStudio(object_id);
      setSection("library");
    } else if (section === "projects") {
      setSection("projects");
    } else if (section === "chat") {
      setSection("chat");
    } else {
      setSection((section as any) || "overview");
    }
  };

  // Record active jump location
  const recordAndJump = async (section: string, object_id: string, label: string, action: () => void) => {
    if (activeTopicId) {
      try {
        await api.updateTopicLocation(activeTopicId, { section, object_id, label });
      } catch {}
    }
    action();
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] w-full overflow-hidden bg-background">
      {/* Left Sidebar: Topics Directory */}
      <aside className="w-80 shrink-0 border-r border-border/70 bg-card/40 flex flex-col h-full">
        {/* Header */}
        <div className="p-3 border-b border-border/60 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Bookmark className="h-4 w-4 text-emerald-500" />
            <div>
              <span className="text-[10px] font-mono tracking-wider uppercase text-foreground/75 font-semibold block">
                Research Topics
              </span>
              <h2 className="text-sm font-bold text-foreground leading-tight">研究专题</h2>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setFormName("");
              setFormObjective("");
              setFormDesc("");
              setFormTags("");
              setIsCreateOpen(true);
            }}
            className="h-7 px-2 text-xs flex items-center space-x-1 border border-border/60"
          >
            <Plus className="h-3.5 w-3.5 text-emerald-500" />
            <span>新建专题</span>
          </Button>
        </div>

        {/* Search & Tag filter */}
        <div className="p-2.5 border-b border-border/60 space-y-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索专题名称、目标或标签..."
              className="w-full pl-8 pr-2.5 py-1.5 text-xs rounded-md bg-muted/50 border border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>

          {allTags.length > 0 && (
            <div className="flex items-center space-x-1 overflow-x-auto py-1 scrollbar-none text-[11px]">
              <button
                onClick={() => setSelectedTag(null)}
                className={cn(
                  "px-2 py-0.5 rounded-full border transition shrink-0",
                  selectedTag === null
                    ? "bg-emerald-500/15 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 font-medium"
                    : "border-border/60 text-muted-foreground hover:bg-muted/40"
                )}
              >
                全部
              </button>
              {allTags.map((tag) => (
                <button
                  key={tag}
                  onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
                  className={cn(
                    "px-2 py-0.5 rounded-full border transition shrink-0",
                    selectedTag === tag
                      ? "bg-emerald-500/15 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 font-medium"
                      : "border-border/60 text-muted-foreground hover:bg-muted/40"
                  )}
                >
                  #{tag}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Topics List */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {filteredTopics.length === 0 ? (
            <div className="text-center py-10 px-4 text-xs text-muted-foreground space-y-2">
              <p>{search || selectedTag ? "没有找到符合条件的专题" : "暂无研究专题"}</p>
              {!search && !selectedTag && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsCreateOpen(true)}
                  className="text-xs"
                >
                  <Plus className="h-3.5 w-3.5 mr-1 text-emerald-500" />
                  新建第一个专题
                </Button>
              )}
            </div>
          ) : (
            filteredTopics.map((topic) => {
              const isSelected = topic.topic_id === activeTopicId;
              const totalItems =
                (topic.document_ids?.length || 0) +
                (topic.note_ids?.length || 0) +
                (topic.run_ids?.length || 0) +
                (topic.project_ids?.length || 0) +
                (topic.conversation_ids?.length || 0);

              return (
                <div
                  key={topic.topic_id}
                  onClick={() => setActiveTopicId(topic.topic_id)}
                  className={cn(
                    "p-3 rounded-lg border transition cursor-pointer text-left space-y-1.5",
                    isSelected
                      ? "bg-accent/70 border-emerald-500/50 shadow-xs"
                      : "bg-card/50 border-border/60 hover:bg-muted/30"
                  )}
                >
                  <div className="flex items-start justify-between">
                    <h3 className="text-xs font-bold text-foreground line-clamp-1">
                      {topic.name}
                    </h3>
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0 font-mono">
                      {totalItems} 项
                    </Badge>
                  </div>

                  {topic.objective && (
                    <p className="text-[11px] text-muted-foreground line-clamp-2 leading-relaxed">
                      {topic.objective}
                    </p>
                  )}

                  <div className="flex items-center justify-between pt-1 text-[10px] text-muted-foreground/75 font-mono">
                    <div className="flex items-center space-x-1.5">
                      {(topic.document_ids?.length || 0) > 0 && <span>📄 {topic.document_ids?.length}</span>}
                      {(topic.run_ids?.length || 0) > 0 && <span>🔬 {topic.run_ids?.length}</span>}
                      {(topic.project_ids?.length || 0) > 0 && <span>💻 {topic.project_ids?.length}</span>}
                    </div>
                    <span>{formatTimeAgo(topic.updated_at)}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </aside>

      {/* Main Board: Cross-Object Workspace */}
      <main className="flex-1 overflow-y-auto flex flex-col bg-background/50">
        {!activeDetail ? (
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center space-y-4">
            <div className="h-16 w-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-500">
              <Bookmark className="h-8 w-8" />
            </div>
            <div className="max-w-md space-y-1.5">
              <h3 className="text-base font-bold text-foreground">选择或新建研究专题</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                研究专题是跨文献、研读笔记、研究报告、代码工程与研讨对话的轻量切片载体。
                聚合已有研究成果，随时回看证据链路并一键恢复工作现场。
              </p>
            </div>
            <Button onClick={() => setIsCreateOpen(true)} className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs">
              <Plus className="h-3.5 w-3.5 mr-1" />
              创建研究专题
            </Button>
          </div>
        ) : (
          <div className="p-6 max-w-6xl w-full mx-auto space-y-6">
            {/* Topic Header Card */}
            <div className="rounded-xl border border-border/80 bg-card/60 p-5 shadow-xs space-y-4 backdrop-blur-xs">
              <div className="flex items-start justify-between">
                <div className="space-y-1">
                  <div className="flex items-center space-x-2">
                    <Badge variant="outline" className="text-[10px] text-emerald-600 dark:text-emerald-400 border-emerald-500/30">
                      专题看板
                    </Badge>
                    <span className="text-xs text-muted-foreground font-mono">
                      {activeDetail.topic_id}
                    </span>
                  </div>
                  <h1 className="text-xl font-bold text-foreground font-serif-academic tracking-tight">
                    {activeDetail.name}
                  </h1>
                </div>

                <div className="flex items-center space-x-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setFormName(activeDetail.name);
                      setFormObjective(activeDetail.objective || "");
                      setFormDesc(activeDetail.description || "");
                      setFormTags((activeDetail.tags || []).join(", "));
                      setIsEditOpen(true);
                    }}
                    className="h-8 text-xs flex items-center space-x-1"
                  >
                    <Edit3 className="h-3.5 w-3.5" />
                    <span>编辑</span>
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleDeleteTopic}
                    className="h-8 text-xs text-rose-500 hover:text-rose-600 hover:bg-rose-500/10 flex items-center space-x-1"
                    title="解除专题（不删除底层数据）"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    <span>解除</span>
                  </Button>
                </div>
              </div>

              {/* Research Objective Box */}
              {activeDetail.objective && (
                <div className="rounded-lg bg-muted/40 border border-border/60 p-3 flex items-start space-x-3">
                  <HelpCircle className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" />
                  <div className="text-xs space-y-0.5">
                    <span className="font-semibold text-foreground">核心研究问题 / 目标：</span>
                    <p className="text-muted-foreground leading-relaxed">{activeDetail.objective}</p>
                  </div>
                </div>
              )}

              {/* Description & Tags */}
              {activeDetail.description && (
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {activeDetail.description}
                </p>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-border/40 text-xs">
                <div className="flex items-center space-x-1.5">
                  {(activeDetail.tags || []).map((t) => (
                    <Badge key={t} variant="secondary" className="text-[10px] font-mono">
                      #{t}
                    </Badge>
                  ))}
                </div>

                {/* Resume Last Location Pill */}
                {activeDetail.recent_location?.section && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleResumeLocation}
                    className="h-7 text-xs border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/20 flex items-center space-x-1.5 font-medium"
                  >
                    <Compass className="h-3.5 w-3.5 animate-spin-slow" />
                    <span>
                      继续上次工作: {activeDetail.recent_location.label || activeDetail.recent_location.section}
                    </span>
                    <ExternalLink className="h-3 w-3" />
                  </Button>
                )}
              </div>
            </div>

            {/* Cross-Object Boards Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {/* 1. Documents Board */}
              <div className="rounded-xl border border-border/70 bg-card/40 p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/50 pb-2.5">
                  <div className="flex items-center space-x-2">
                    <BookOpen className="h-4 w-4 text-sky-500" />
                    <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      文献资料 ({activeDetail.documents?.length || 0})
                    </h3>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setPickerTab("document");
                      setIsPickerOpen(true);
                    }}
                    className="h-6 px-2 text-[11px] text-sky-600 dark:text-sky-400 hover:bg-sky-500/10"
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    关联文献
                  </Button>
                </div>

                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {(!activeDetail.documents || activeDetail.documents.length === 0) ? (
                    <p className="text-xs text-muted-foreground py-4 text-center">暂未关联文献</p>
                  ) : (
                    activeDetail.documents.map((doc) => (
                      <div
                        key={doc.document_id}
                        className="p-2.5 rounded-lg border border-border/50 bg-background/60 flex items-center justify-between group hover:border-sky-500/40 transition text-xs"
                      >
                        <div
                          className="flex items-center space-x-2 min-w-0 flex-1 cursor-pointer"
                          onClick={() =>
                            recordAndJump("library", doc.document_id, doc.title, () => {
                              openNoteStudio(doc.document_id);
                              setSection("library");
                            })
                          }
                        >
                          <BookOpen className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          <span className="font-medium text-foreground truncate">{doc.title}</span>
                          {!doc.available && (
                            <Badge variant="destructive" className="text-[9px] px-1 py-0">已失效</Badge>
                          )}
                        </div>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handleLinkObject("document", doc.document_id, "unlink")}
                          className="opacity-0 group-hover:opacity-100 transition text-muted-foreground hover:text-rose-500 h-6 w-6"
                          title="解除关联"
                        >
                          <Unlink className="h-3 w-3" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* 2. Research Notes Board */}
              <div className="rounded-xl border border-border/70 bg-card/40 p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/50 pb-2.5">
                  <div className="flex items-center space-x-2">
                    <FileText className="h-4 w-4 text-amber-500" />
                    <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      研读笔记 ({activeDetail.notes?.length || 0})
                    </h3>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setPickerTab("note");
                      setIsPickerOpen(true);
                    }}
                    className="h-6 px-2 text-[11px] text-amber-600 dark:text-amber-400 hover:bg-amber-500/10"
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    关联笔记
                  </Button>
                </div>

                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {(!activeDetail.notes || activeDetail.notes.length === 0) ? (
                    <p className="text-xs text-muted-foreground py-4 text-center">暂未关联笔记</p>
                  ) : (
                    activeDetail.notes.map((note) => (
                      <div
                        key={note.note_id}
                        className="p-2.5 rounded-lg border border-border/50 bg-background/60 flex items-center justify-between group hover:border-amber-500/40 transition text-xs"
                      >
                        <div
                          className="flex items-center space-x-2 min-w-0 flex-1 cursor-pointer"
                          onClick={() =>
                            recordAndJump("library", note.document_id || note.note_id, note.title, () => {
                              openNoteStudio(note.document_id || note.note_id);
                            })
                          }
                        >
                          <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          <span className="font-medium text-foreground truncate">{note.title}</span>
                          <Badge variant="outline" className="text-[9px] px-1 py-0 font-mono">
                            v{note.version}
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handleLinkObject("note", note.note_id, "unlink")}
                          className="opacity-0 group-hover:opacity-100 transition text-muted-foreground hover:text-rose-500 h-6 w-6"
                          title="解除关联"
                        >
                          <Unlink className="h-3 w-3" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* 3. Research Runs & Tasks Board */}
              <div className="rounded-xl border border-border/70 bg-card/40 p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/50 pb-2.5">
                  <div className="flex items-center space-x-2">
                    <FlaskConical className="h-4 w-4 text-emerald-500" />
                    <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      研究报告 ({activeDetail.runs?.length || 0})
                    </h3>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setPickerTab("run");
                      setIsPickerOpen(true);
                    }}
                    className="h-6 px-2 text-[11px] text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10"
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    关联报告
                  </Button>
                </div>

                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {(!activeDetail.runs || activeDetail.runs.length === 0) ? (
                    <p className="text-xs text-muted-foreground py-4 text-center">暂未关联研究任务</p>
                  ) : (
                    activeDetail.runs.map((r) => (
                      <div
                        key={r.run_id}
                        className="p-2.5 rounded-lg border border-border/50 bg-background/60 flex items-center justify-between group hover:border-emerald-500/40 transition text-xs"
                      >
                        <div
                          className="flex items-center space-x-2 min-w-0 flex-1 cursor-pointer"
                          onClick={() =>
                            recordAndJump("research", r.run_id, r.title, () => {
                              setActiveRunId(r.run_id);
                              setSection("research");
                            })
                          }
                        >
                          <FlaskConical className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          <span className="font-medium text-foreground truncate">{r.title}</span>
                          <Badge variant="outline" className="text-[9px] px-1 py-0 font-mono">
                            {r.state}
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handleLinkObject("run", r.run_id, "unlink")}
                          className="opacity-0 group-hover:opacity-100 transition text-muted-foreground hover:text-rose-500 h-6 w-6"
                          title="解除关联"
                        >
                          <Unlink className="h-3 w-3" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* 4. Code Projects Board */}
              <div className="rounded-xl border border-border/70 bg-card/40 p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-border/50 pb-2.5">
                  <div className="flex items-center space-x-2">
                    <FolderGit2 className="h-4 w-4 text-violet-500" />
                    <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      代码工程 ({activeDetail.projects?.length || 0})
                    </h3>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setPickerTab("project");
                      setIsPickerOpen(true);
                    }}
                    className="h-6 px-2 text-[11px] text-violet-600 dark:text-violet-400 hover:bg-violet-500/10"
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    关联项目
                  </Button>
                </div>

                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {(!activeDetail.projects || activeDetail.projects.length === 0) ? (
                    <p className="text-xs text-muted-foreground py-4 text-center">暂未关联代码项目</p>
                  ) : (
                    activeDetail.projects.map((p) => (
                      <div
                        key={p.project_id}
                        className="p-2.5 rounded-lg border border-border/50 bg-background/60 flex items-center justify-between group hover:border-violet-500/40 transition text-xs"
                      >
                        <div
                          className="flex items-center space-x-2 min-w-0 flex-1 cursor-pointer"
                          onClick={() =>
                            recordAndJump("projects", p.project_id, p.name, () => {
                              setSection("projects");
                            })
                          }
                        >
                          <FolderGit2 className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          <span className="font-medium text-foreground truncate">{p.name}</span>
                          <span className="text-[10px] text-muted-foreground/70 font-mono truncate max-w-[120px]">
                            {p.root_path}
                          </span>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handleLinkObject("project", p.project_id, "unlink")}
                          className="opacity-0 group-hover:opacity-100 transition text-muted-foreground hover:text-rose-500 h-6 w-6"
                          title="解除关联"
                        >
                          <Unlink className="h-3 w-3" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* 5. Chat Conversations Board */}
              <div className="rounded-xl border border-border/70 bg-card/40 p-4 space-y-3 md:col-span-2">
                <div className="flex items-center justify-between border-b border-border/50 pb-2.5">
                  <div className="flex items-center space-x-2">
                    <MessageSquare className="h-4 w-4 text-indigo-500" />
                    <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">
                      研讨对话 ({activeDetail.conversations?.length || 0})
                    </h3>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setPickerTab("conversation");
                      setIsPickerOpen(true);
                    }}
                    className="h-6 px-2 text-[11px] text-indigo-600 dark:text-indigo-400 hover:bg-indigo-500/10"
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    关联对话
                  </Button>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-48 overflow-y-auto pr-1">
                  {(!activeDetail.conversations || activeDetail.conversations.length === 0) ? (
                    <p className="text-xs text-muted-foreground py-4 text-center sm:col-span-2">暂未关联研讨会话</p>
                  ) : (
                    activeDetail.conversations.map((c) => (
                      <div
                        key={c.conversation_id}
                        className="p-2.5 rounded-lg border border-border/50 bg-background/60 flex items-center justify-between group hover:border-indigo-500/40 transition text-xs"
                      >
                        <div
                          className="flex items-center space-x-2 min-w-0 flex-1 cursor-pointer"
                          onClick={() =>
                            recordAndJump("chat", c.conversation_id, c.title, () => {
                              setSection("chat");
                            })
                          }
                        >
                          <MessageSquare className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                          <span className="font-medium text-foreground truncate">{c.title}</span>
                          <span className="text-[10px] text-muted-foreground/70 font-mono">
                            {c.message_count} 条
                          </span>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handleLinkObject("conversation", c.conversation_id, "unlink")}
                          className="opacity-0 group-hover:opacity-100 transition text-muted-foreground hover:text-rose-500 h-6 w-6"
                          title="解除关联"
                        >
                          <Unlink className="h-3 w-3" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Create / Edit Topic Dialog */}
      {(isCreateOpen || isEditOpen) && (
        <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-card border border-border rounded-xl shadow-xl max-w-lg w-full p-5 space-y-4">
            <div className="flex items-center justify-between border-b border-border/60 pb-3">
              <h3 className="text-sm font-bold text-foreground">
                {isCreateOpen ? "新建研究专题" : "编辑研究专题"}
              </h3>
              <button
                onClick={() => {
                  setIsCreateOpen(false);
                  setIsEditOpen(false);
                }}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={isCreateOpen ? handleCreateTopic : handleEditTopic} className="space-y-3 text-xs">
              <div className="space-y-1">
                <label className="font-semibold text-foreground">专题名称 *</label>
                <input
                  type="text"
                  required
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="如：大模型代码验证与受控重构研究"
                  className="w-full px-3 py-1.5 rounded-md bg-muted/50 border border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>

              <div className="space-y-1">
                <label className="font-semibold text-foreground">核心研究问题 / 目标</label>
                <textarea
                  rows={3}
                  value={formObjective}
                  onChange={(e) => setFormObjective(e.target.value)}
                  placeholder="如：评估 AST 语义切片与 Git 沙箱回滚机制在多智能体协作下的正确率"
                  className="w-full px-3 py-1.5 rounded-md bg-muted/50 border border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>

              <div className="space-y-1">
                <label className="font-semibold text-foreground">背景描述 (可选)</label>
                <textarea
                  rows={2}
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="专题补充背景信息、研究假设或预期产物..."
                  className="w-full px-3 py-1.5 rounded-md bg-muted/50 border border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>

              <div className="space-y-1">
                <label className="font-semibold text-foreground">标签 (逗号分隔)</label>
                <input
                  type="text"
                  value={formTags}
                  onChange={(e) => setFormTags(e.target.value)}
                  placeholder="agent, coding, benchmark"
                  className="w-full px-3 py-1.5 rounded-md bg-muted/50 border border-input focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>

              <div className="flex items-center justify-end space-x-2 pt-2 border-t border-border/40">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setIsCreateOpen(false);
                    setIsEditOpen(false);
                  }}
                >
                  取消
                </Button>
                <Button type="submit" size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white">
                  {isCreateOpen ? "创建专题" : "保存修改"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Object Link Picker Dialog */}
      {isPickerOpen && activeDetail && (
        <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-card border border-border rounded-xl shadow-xl max-w-xl w-full p-5 space-y-4 max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between border-b border-border/60 pb-3">
              <h3 className="text-sm font-bold text-foreground">关联已有对象至专题</h3>
              <button onClick={() => setIsPickerOpen(false)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Category Tabs */}
            <div className="flex items-center space-x-1 bg-muted/50 p-1 rounded-lg text-xs">
              {(["document", "note", "run", "project", "conversation"] as const).map((tab) => {
                const labelMap = {
                  document: "文献资料",
                  note: "研读笔记",
                  run: "研究任务",
                  project: "代码项目",
                  conversation: "研讨对话",
                };
                return (
                  <button
                    key={tab}
                    onClick={() => setPickerTab(tab)}
                    className={cn(
                      "flex-1 py-1 rounded-md font-medium transition text-center",
                      pickerTab === tab ? "bg-background text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {labelMap[tab]}
                  </button>
                );
              })}
            </div>

            {/* List pool */}
            <div className="flex-1 overflow-y-auto space-y-1.5 p-1 min-h-[220px]">
              {pickerTab === "document" && (
                availableDocs.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-8 text-center">资料库中暂无可用文献</p>
                ) : (
                  availableDocs.map((d) => {
                    const isLinked = (activeDetail.document_ids || []).includes(d.document_id);
                    return (
                      <div key={d.document_id} className="p-2.5 rounded-lg border border-border/50 flex items-center justify-between text-xs">
                        <span className="font-medium text-foreground truncate max-w-md">{d.title}</span>
                        <Button
                          variant={isLinked ? "outline" : "default"}
                          size="sm"
                          disabled={isLinked}
                          onClick={() => handleLinkObject("document", d.document_id, "link")}
                          className="h-6 text-[11px]"
                        >
                          {isLinked ? "已关联" : "+ 关联"}
                        </Button>
                      </div>
                    );
                  })
                )
              )}

              {pickerTab === "note" && (
                availableNotes.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-8 text-center">暂无可用笔记</p>
                ) : (
                  availableNotes.map((n) => {
                    const isLinked = (activeDetail.note_ids || []).includes(n.note_id);
                    return (
                      <div key={n.note_id} className="p-2.5 rounded-lg border border-border/50 flex items-center justify-between text-xs">
                        <span className="font-medium text-foreground truncate max-w-md">{n.title}</span>
                        <Button
                          variant={isLinked ? "outline" : "default"}
                          size="sm"
                          disabled={isLinked}
                          onClick={() => handleLinkObject("note", n.note_id, "link")}
                          className="h-6 text-[11px]"
                        >
                          {isLinked ? "已关联" : "+ 关联"}
                        </Button>
                      </div>
                    );
                  })
                )
              )}

              {pickerTab === "run" && (
                availableRuns.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-8 text-center">暂无可用研究任务</p>
                ) : (
                  availableRuns.map((r) => {
                    const isLinked = (activeDetail.run_ids || []).includes(r.run_id);
                    return (
                      <div key={r.run_id} className="p-2.5 rounded-lg border border-border/50 flex items-center justify-between text-xs">
                        <span className="font-medium text-foreground truncate max-w-md">{r.title || r.query || r.run_id}</span>
                        <Button
                          variant={isLinked ? "outline" : "default"}
                          size="sm"
                          disabled={isLinked}
                          onClick={() => handleLinkObject("run", r.run_id, "link")}
                          className="h-6 text-[11px]"
                        >
                          {isLinked ? "已关联" : "+ 关联"}
                        </Button>
                      </div>
                    );
                  })
                )
              )}

              {pickerTab === "project" && (
                availableProjects.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-8 text-center">暂无可用代码工程</p>
                ) : (
                  availableProjects.map((p) => {
                    const isLinked = (activeDetail.project_ids || []).includes(p.project_id);
                    return (
                      <div key={p.project_id} className="p-2.5 rounded-lg border border-border/50 flex items-center justify-between text-xs">
                        <span className="font-medium text-foreground truncate max-w-md">{p.name} ({p.root_path})</span>
                        <Button
                          variant={isLinked ? "outline" : "default"}
                          size="sm"
                          disabled={isLinked}
                          onClick={() => handleLinkObject("project", p.project_id, "link")}
                          className="h-6 text-[11px]"
                        >
                          {isLinked ? "已关联" : "+ 关联"}
                        </Button>
                      </div>
                    );
                  })
                )
              )}

              {pickerTab === "conversation" && (
                availableConvs.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-8 text-center">暂无可用会话记录</p>
                ) : (
                  availableConvs.map((c) => {
                    const isLinked = (activeDetail.conversation_ids || []).includes(c.conversation_id);
                    return (
                      <div key={c.conversation_id} className="p-2.5 rounded-lg border border-border/50 flex items-center justify-between text-xs">
                        <span className="font-medium text-foreground truncate max-w-md">{c.title}</span>
                        <Button
                          variant={isLinked ? "outline" : "default"}
                          size="sm"
                          disabled={isLinked}
                          onClick={() => handleLinkObject("conversation", c.conversation_id, "link")}
                          className="h-6 text-[11px]"
                        >
                          {isLinked ? "已关联" : "+ 关联"}
                        </Button>
                      </div>
                    );
                  })
                )
              )}
            </div>

            <div className="flex items-center justify-end pt-2 border-t border-border/40">
              <Button size="sm" onClick={() => setIsPickerOpen(false)}>
                完成
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
