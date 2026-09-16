import React, { useState, useEffect, useRef } from "react";
import {
  FolderGit2,
  GitBranch,
  FileCode,
  Folder,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  Terminal,
  Play,
  Check,
  Plus,
  RefreshCw,
  Copy,
  Eye,
  Code2,
  FolderSearch,
  X,
  Layers,
  ShieldCheck,
  Activity,
  FileText,
  AlertTriangle,
  Network,
  ArrowRight,
  Sparkles,
  CheckCircle2,
  Info,
  ExternalLink,
  ShieldAlert,
  Scale,
  BookOpen,
  Send,
  Bot,
  User,
  MessageSquare,
  Wand2,
  Trash2,
  GitCompare,
} from "lucide-react";
import Prism from "prismjs";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-typescript";
import "prismjs/components/prism-jsx";
import "prismjs/components/prism-tsx";
import "prismjs/components/prism-python";
import "prismjs/components/prism-json";
import "prismjs/components/prism-markdown";
import "prismjs/components/prism-yaml";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-css";
import "prismjs/components/prism-sql";
import "@/styles/prism-academic-contrast.css";
import { renderMarkdownWithMath } from "@/lib/math";
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
import { cn } from "@/lib/utils";
import type { ProjectSummary } from "@/types/workbench";

function getLanguageFromPath(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  switch (ext) {
    case "py":
      return "python";
    case "ts":
      return "typescript";
    case "tsx":
      return "tsx";
    case "js":
      return "javascript";
    case "jsx":
      return "jsx";
    case "json":
      return "json";
    case "md":
      return "markdown";
    case "yaml":
    case "yml":
      return "yaml";
    case "sh":
    case "bash":
      return "bash";
    case "css":
      return "css";
    case "sql":
      return "sql";
    default:
      return "text";
  }
}

function highlightCode(code: string, lang: string): string {
  if (lang && Prism.languages[lang]) {
    try {
      return Prism.highlight(code, Prism.languages[lang], lang);
    } catch {
      return code;
    }
  }
  return code;
}

interface FileTreeNodeProps {
  node: {
    name: string;
    path: string;
    is_dir: boolean;
    size_bytes?: number;
    children?: any[];
  };
  selectedFile: string;
  onSelectFile: (filePath: string) => void;
  depth?: number;
}

const FileTreeNode: React.FC<FileTreeNodeProps> = ({
  node,
  selectedFile,
  onSelectFile,
  depth = 0,
}) => {
  const [isOpen, setIsOpen] = useState(depth < 1);

  if (node.is_dir) {
    const hasChildren = node.children && node.children.length > 0;
    return (
      <div className="select-none">
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          style={{ paddingLeft: `${depth * 12 + 6}px` }}
          className="w-full text-left py-1 pr-2 rounded flex items-center space-x-1.5 transition text-foreground/80 hover:bg-muted/60 hover:text-foreground text-xs"
        >
          {hasChildren ? (
            isOpen ? (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            )
          ) : (
            <span className="w-3.5 inline-block" />
          )}
          {isOpen ? (
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-500" />
          ) : (
            <Folder className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-500" />
          )}
          <span className="truncate font-medium">{node.name}</span>
        </button>

        {isOpen && hasChildren && (
          <div className="space-y-0.5">
            {node.children!.map((child, idx) => (
              <FileTreeNode
                key={child.path || idx}
                node={child}
                selectedFile={selectedFile}
                onSelectFile={onSelectFile}
                depth={depth + 1}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  const isSelected = selectedFile === node.path;
  const formatSize = (sz?: number) => {
    if (!sz) return "";
    if (sz < 1024) return `${sz}B`;
    return `${(sz / 1024).toFixed(1)}K`;
  };

  return (
    <button
      type="button"
      onClick={() => onSelectFile(node.path)}
      style={{ paddingLeft: `${depth * 12 + 20}px` }}
      className={cn(
        "w-full text-left py-1 pr-2 rounded flex items-center justify-between space-x-2 transition text-xs truncate group",
        isSelected
          ? "bg-emerald-800 text-white font-semibold shadow-2xs"
          : "text-foreground/75 hover:text-foreground hover:bg-muted/50"
      )}
    >
      <div className="flex items-center space-x-1.5 truncate">
        <FileCode
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            isSelected ? "text-white" : "text-emerald-700 dark:text-emerald-400"
          )}
        />
        <span className="truncate">{node.name}</span>
      </div>
      {node.size_bytes !== undefined && node.size_bytes > 0 && (
        <span
          className={cn(
            "text-[10px] font-mono shrink-0 pr-1",
            isSelected ? "text-white/80" : "text-muted-foreground group-hover:text-foreground/80"
          )}
        >
          {formatSize(node.size_bytes)}
        </span>
      )}
    </button>
  );
};

export type WorkspaceTabType =
  | "code"
  | "audit"
  | "learning"
  | "walkthrough"
  | "theory"
  | "patch"
  | "diff";

interface CopilotMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  targetTab?: WorkspaceTabType;
  citedFiles?: string[];
}

export const ProjectsView: React.FC = () => {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [tree, setTree] = useState<any[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>("");
  const [fileContent, setFileContent] = useState<string>("");
  const [diff, setDiff] = useState<string>("");
  const [semanticDiff, setSemanticDiff] = useState<any>(null);
  const [diffLoading, setDiffLoading] = useState<boolean>(false);
  const [compareBranch, setCompareBranch] = useState<string>("main");
  const [codingPrompt, setCodingPrompt] = useState<string>("" );
  const [proposal, setProposal] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [treeLoading, setTreeLoading] = useState(false);

  // Central Workspace View Tab
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTabType>("code");

  // Governance & Analysis Data
  const [auditData, setAuditData] = useState<any>(null);
  const [walkthroughData, setWalkthroughData] = useState<any>(null);
  const [learningData, setLearningData] = useState<any>(null);
  const [govLoading, setGovLoading] = useState<boolean>(false);
  const [learningLoading, setLearningLoading] = useState<boolean>(false);

  // Preview / Code view state in code tab
  const [viewMode, setViewMode] = useState<"code" | "preview">("code");
  const [copiedFile, setCopiedFile] = useState(false);

  // AI Copilot Dialogue State
  const [copilotMessages, setCopilotMessages] = useState<CopilotMessage[]>([]);
  const [copilotInput, setCopilotInput] = useState<string>("");
  const [copilotLoading, setCopilotLoading] = useState<boolean>(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const copilotTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Code Snippet Selection State for Quote-to-Copilot
  const [selectedSnippet, setSelectedSnippet] = useState<string>("");
  const [selectionPos, setSelectionPos] = useState<{ x: number; y: number } | null>(null);

  // Add Project Dialog State
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [addName, setAddName] = useState("");
  const [addPaths, setAddPaths] = useState<string[]>([]);
  const [manualPathInput, setManualPathInput] = useState("");
  const [addDesc, setAddDesc] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [submittingAdd, setSubmittingAdd] = useState(false);
  const [browsingFolder, setBrowsingFolder] = useState(false);

  // In-modal Directory Navigator
  const [showFsBrowser, setShowFsBrowser] = useState<boolean>(false);
  const [fsDrives, setFsDrives] = useState<string[]>([]);
  const [fsQuickRoots, setFsQuickRoots] = useState<string[]>([]);
  const [currentFsPath, setCurrentFsPath] = useState<string>("");
  const [currentFsDirs, setCurrentFsDirs] = useState<Array<{ name: string; path: string }>>([]);
  const [currentFsParent, setCurrentFsParent] = useState<string | null>(null);
  const [fsBrowserLoading, setFsBrowserLoading] = useState<boolean>(false);

  const loadProjects = async (selectId?: string) => {
    try {
      const res = await api.getProjects();
      const list = res || [];
      setProjects(list);
      if (selectId) {
        setSelectedProjectId(selectId);
      } else if (list.length > 0 && !selectedProjectId) {
        setSelectedProjectId(list[0].project_id);
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    loadProjects();
  }, []);

  const handleRefreshGovernance = async () => {
    if (!selectedProjectId) return;
    setGovLoading(true);
    setLearningLoading(true);
    try {
      const [auditRes, walkRes, learnRes] = await Promise.allSettled([
        api.getProjectAudit(selectedProjectId),
        api.getProjectWalkthrough(selectedProjectId),
        api.getProjectLearningGuide(selectedProjectId),
      ]);
      if (auditRes.status === "fulfilled") setAuditData(auditRes.value);
      if (walkRes.status === "fulfilled") setWalkthroughData(walkRes.value);
      if (learnRes.status === "fulfilled") setLearningData(learnRes.value);
    } finally {
      setGovLoading(false);
      setLearningLoading(false);
    }
  };

  useEffect(() => {
    if (!selectedProjectId) return;
    setTreeLoading(true);
    setSelectedFile("");
    setFileContent("");
    setProposal(null);
    setCodingPrompt("");
    setDiff("");
    setAuditData(null);
    setWalkthroughData(null);
    setLearningData(null);
    setCopilotMessages([]);

    api
      .getProjectTree(selectedProjectId)
      .then((res) => {
        setTree(res.items || res.tree || []);
      })
      .catch(() => setTree([]))
      .finally(() => setTreeLoading(false));

    fetchSemanticDiff(compareBranch);

    setGovLoading(true);
    setLearningLoading(true);
    api
      .getProjectAudit(selectedProjectId)
      .then((res) => setAuditData(res))
      .catch(() => setAuditData(null));

    api
      .getProjectWalkthrough(selectedProjectId)
      .then((res) => setWalkthroughData(res))
      .catch(() => setWalkthroughData(null))
      .finally(() => setGovLoading(false));

    api
      .getProjectLearningGuide(selectedProjectId)
      .then((res) => setLearningData(res))
      .catch(() => setLearningData(null))
      .finally(() => setLearningLoading(false));
  }, [selectedProjectId]);

  const fetchSemanticDiff = async (branch?: string) => {
    if (!selectedProjectId) return;
    const b = branch !== undefined ? branch : compareBranch;
    setDiffLoading(true);
    try {
      const res = await api.getProjectSemanticDiff(selectedProjectId, b);
      setSemanticDiff(res);
      setDiff(res?.diff || "");
    } catch {
      setSemanticDiff(null);
      setDiff("");
    } finally {
      setDiffLoading(false);
    }
  };

  useEffect(() => {
    const handleSelectionChange = () => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.toString().trim()) {
        setSelectionPos(null);
        setSelectedSnippet("");
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectionPos(null);
        setSelectedSnippet("");
      }
    };
    document.addEventListener("selectionchange", handleSelectionChange);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("selectionchange", handleSelectionChange);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [copilotMessages, copilotLoading]);

  const handleSelectFile = async (filePath: string) => {
    setSelectedFile(filePath);
    setWorkspaceTab("code");
    setViewMode("code");
    setCopiedFile(false);
    try {
      const res = await api.getProjectFile(selectedProjectId, filePath);
      setFileContent(res.content || "");
    } catch {
      setFileContent("// 无法读取该文件内容或文件为空");
    }
  };

  const handleCopyFile = () => {
    if (fileContent) {
      navigator.clipboard.writeText(fileContent);
      setCopiedFile(true);
      setTimeout(() => setCopiedFile(false), 2000);
    }
  };

  const calcLineRange = (content: string, snippet: string): { startLine: number; endLine: number } => {
    if (!snippet || !content) return { startLine: 1, endLine: 1 };
    const trimmed = snippet.trim();
    let idx = content.indexOf(trimmed);
    if (idx === -1) {
      const firstLine = trimmed.split("\n")[0].trim();
      idx = firstLine ? content.indexOf(firstLine) : -1;
    }
    if (idx === -1) return { startLine: 1, endLine: 1 };
    const startLine = content.substring(0, idx).split("\n").length;
    const linesCount = trimmed.split("\n").length;
    const endLine = startLine + linesCount - 1;
    return { startLine, endLine };
  };

  const handleCodeMouseUp = () => {
    const sel = window.getSelection();
    const text = sel ? sel.toString().trim() : "";
    if (text && text.length > 0) {
      setSelectedSnippet(text);
      const range = sel?.getRangeAt(0);
      const rect = range?.getBoundingClientRect();
      if (rect) {
        setSelectionPos({
          x: rect.left + rect.width / 2,
          y: Math.max(10, rect.top - 10),
        });
      }
    } else {
      setSelectedSnippet("");
      setSelectionPos(null);
    }
  };

  const handleQuoteToCopilot = (snippetToQuote?: string) => {
    const snippet = snippetToQuote !== undefined ? snippetToQuote : selectedSnippet;
    let citationBadge = "";
    if (snippet && snippet.trim() && selectedFile) {
      const { startLine, endLine } = calcLineRange(fileContent, snippet);
      const lineTag = startLine === endLine ? `${startLine}行` : `${startLine}行-${endLine}行`;
      citationBadge = `[${selectedFile}-${lineTag}]`;
    } else if (selectedFile) {
      citationBadge = `[${selectedFile}]`;
    } else {
      return;
    }

    setCopilotInput((prev) => {
      const trimmed = prev.trim();
      if (!trimmed) {
        return `${citationBadge} 请帮我分析：`;
      }
      return `${trimmed} ${citationBadge} `;
    });

    window.getSelection()?.removeAllRanges();
    setSelectedSnippet("");
    setSelectionPos(null);

    setTimeout(() => {
      if (copilotTextareaRef.current) {
        copilotTextareaRef.current.focus();
        copilotTextareaRef.current.scrollTop = copilotTextareaRef.current.scrollHeight;
      }
    }, 100);
  };

  const handleBrowseFolder = async () => {
    setBrowsingFolder(true);
    setAddError(null);
    try {
      const res = await api.browseFolder();
      if (res && res.path) {
        if (!addPaths.includes(res.path)) {
          const next = [...addPaths, res.path];
          setAddPaths(next);
          if (!addName.trim()) {
            const parts = res.path.split(/[/\\]/).filter(Boolean);
            if (parts.length > 0) setAddName(parts[parts.length - 1]);
          }
        }
      } else {
        setAddError("未从文件资源管理器选择文件夹或已取消。如系统弹窗被挡在后台，可检查任务栏，或展开下方【本地磁盘与目录浏览器】直接点选目录。");
      }
    } catch (err: any) {
      setAddError(`无法打开系统资源管理器 (${err.message})。您可以展开下方【本地磁盘与目录浏览器】直接点选目录，或手动输入绝对路径。`);
    } finally {
      setBrowsingFolder(false);
    }
  };

  const toggleFsBrowser = async () => {
    const nextState = !showFsBrowser;
    setShowFsBrowser(nextState);
    if (nextState && fsDrives.length === 0) {
      setFsBrowserLoading(true);
      try {
        const res = await api.getFsDrives();
        setFsDrives(res.drives || []);
        setFsQuickRoots(res.quick_roots || []);
        if (res.quick_roots && res.quick_roots.length > 0) {
          navigateFsDir(res.quick_roots[0]);
        } else if (res.drives && res.drives.length > 0) {
          navigateFsDir(res.drives[0]);
        }
      } catch {
        // ignore
      } finally {
        setFsBrowserLoading(false);
      }
    }
  };

  const navigateFsDir = async (targetPath: string) => {
    if (!targetPath) return;
    setFsBrowserLoading(true);
    try {
      const res = await api.getFsDirs(targetPath);
      if (res.exists) {
        setCurrentFsPath(res.path);
        setCurrentFsParent(res.parent || null);
        setCurrentFsDirs(res.dirs || []);
      }
    } catch {
      // ignore
    } finally {
      setFsBrowserLoading(false);
    }
  };

  const handleSelectFsDir = (dirPath: string) => {
    if (!addPaths.includes(dirPath)) {
      const next = [...addPaths, dirPath];
      setAddPaths(next);
      if (!addName.trim()) {
        const parts = dirPath.split(/[/\\]/).filter(Boolean);
        if (parts.length > 0) setAddName(parts[parts.length - 1]);
      }
    }
  };

  const handleAddManualPath = () => {
    const p = manualPathInput.trim();
    if (!p) return;
    if (!addPaths.includes(p)) {
      const next = [...addPaths, p];
      setAddPaths(next);
      setManualPathInput("");
      if (!addName.trim()) {
        const parts = p.split(/[/\\]/).filter(Boolean);
        if (parts.length > 0) setAddName(parts[parts.length - 1]);
      }
    }
  };

  const handleRemovePath = (idx: number) => {
    setAddPaths((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleProposeCoding = async (customPrompt?: string) => {
    const p = (customPrompt || codingPrompt).trim();
    if (!p || !selectedProjectId) return;
    setLoading(true);
    try {
      const res = await api.proposeProjectCoding(selectedProjectId, p);
      setProposal(res);
      setWorkspaceTab("patch");
    } catch (e: any) {
      alert(`提案失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleApplyCoding = async () => {
    if (!proposal || !selectedProjectId) return;
    setLoading(true);
    try {
      const cleanProposal = { ...proposal };
      if (cleanProposal.target_file) {
        cleanProposal.target_file = cleanProposal.target_file.replace(/[:#]L?\d+$/, "").trim();
      }
      await api.applyProjectCoding(selectedProjectId, cleanProposal);
      alert("补丁变更已成功应用并写入！");
      setProposal(null);
      setCodingPrompt("");
      if (selectedFile) {
        handleSelectFile(selectedFile);
      }
    } catch (e: any) {
      alert(`应用失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    const allPaths = [...addPaths];
    if (manualPathInput.trim() && !allPaths.includes(manualPathInput.trim())) {
      allPaths.push(manualPathInput.trim());
    }
    if (!addName.trim() || allPaths.length === 0) {
      setAddError("请至少选择或输入一个有效的工程本地根目录路径。");
      return;
    }

    setSubmittingAdd(true);
    setAddError(null);
    try {
      const res = await api.createProject({
        name: addName.trim(),
        root_path: allPaths[0],
        root_paths: allPaths,
        description: addDesc.trim(),
      });
      setIsAddOpen(false);
      setAddName("");
      setAddPaths([]);
      setManualPathInput("");
      setAddDesc("");
      await loadProjects(res.project_id);
    } catch (err: any) {
      setAddError(err.message || "添加项目失败，请确认本地路径是否真实存在。");
    } finally {
      setSubmittingAdd(false);
    }
  };

  // AI Project Copilot free-form requirement dispatcher
  const handleSendCopilot = async (inputQuery?: string) => {
    const q = (inputQuery !== undefined ? inputQuery : copilotInput).trim();
    if (!q || !selectedProjectId) return;

    const userMsg: CopilotMessage = {
      id: Date.now().toString(),
      role: "user",
      content: q,
      timestamp: new Date().toLocaleTimeString(),
    };
    setCopilotMessages((prev) => [...prev, userMsg]);
    if (inputQuery === undefined) setCopilotInput("");
    setCopilotLoading(true);

    const qLower = q.toLowerCase();

    try {
      // 1. Audit intent
      if (
        qLower.includes("体检") ||
        qLower.includes("审计") ||
        qLower.includes("健康度") ||
        qLower.includes("隐患") ||
        qLower.includes("规范") ||
        qLower.includes("audit") ||
        qLower.includes("health")
      ) {
        let currentAudit = auditData;
        if (!currentAudit) {
          currentAudit = await api.getProjectAudit(selectedProjectId);
          setAuditData(currentAudit);
        }
        setWorkspaceTab("audit");
        const findingsCount = currentAudit?.findings?.length || 0;
        const hScore = currentAudit?.health_score ?? 90;
        const iScore = currentAudit?.implementation_score ?? 85;

        setCopilotMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            content: `已为您完成工程健康与规范体检！\n\n- **健康度评分**：\`${hScore}/100\`\n- **契约实现度**：\`${iScore}/100\`\n- **发现事项**：共检测到 **${findingsCount}** 处治理建议与风险点。\n\n已自动为您在中央视窗展开【架构体检报告】，您可以点击具体条目快速定位源码，或一键带入补丁修复。`,
            timestamp: new Date().toLocaleTimeString(),
            targetTab: "audit",
          },
        ]);
        return;
      }

      // 2. Learning / Guide intent
      if (
        qLower.includes("导学") ||
        qLower.includes("学习") ||
        qLower.includes("剖析") ||
        qLower.includes("精读") ||
        qLower.includes("路线") ||
        qLower.includes("入门") ||
        qLower.includes("新手") ||
        qLower.includes("learn") ||
        qLower.includes("roadmap") ||
        qLower.includes("guide")
      ) {
        let currentLearn = learningData;
        if (!currentLearn) {
          currentLearn = await api.getProjectLearningGuide(selectedProjectId);
          setLearningData(currentLearn);
        }
        setWorkspaceTab("learning");
        const roadmapCount = currentLearn?.progressive_reading_roadmap?.length || 0;
        const lexiconCount = currentLearn?.lexicon?.length || 0;

        setCopilotMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            content: `已为您生成该项目的导学剖析与精读全景！\n\n- **主技术栈**：\`${currentLearn?.ecosystem?.primary_stack || "Multi-Stack"}\`\n- **精读路线图**：涵盖 **${roadmapCount}** 个循序渐进的学习阶段\n- **核心类库符号**：已提取 **${lexiconCount}** 项关键函数与模块\n\n已在中央视窗为您呈现【导学剖析全景】，点击任意阶段文件即可立即定位阅读。`,
            timestamp: new Date().toLocaleTimeString(),
            targetTab: "learning",
          },
        ]);
        return;
      }

      // 3. Walkthrough / Architecture topology intent
      if (
        qLower.includes("走查") ||
        qLower.includes("拓扑") ||
        qLower.includes("架构") ||
        qLower.includes("数据流") ||
        qLower.includes("组件") ||
        qLower.includes("walkthrough") ||
        qLower.includes("topology")
      ) {
        let currentWalk = walkthroughData;
        if (!currentWalk) {
          currentWalk = await api.getProjectWalkthrough(selectedProjectId);
          setWalkthroughData(currentWalk);
        }
        setWorkspaceTab("walkthrough");
        const compCount = currentWalk?.components?.length || 0;

        setCopilotMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            content: `已完成工程分层走查与拓扑解析！\n\n- **核心分层组件**：共识别 **${compCount}** 个子模块\n- **数据流闭环**：${currentWalk?.data_flow_description ? "已梳理全流程交互闭环" : "已构建数据链路"}\n\n已在中央视窗为您展开【架构组件走查】与 Mermaid 拓扑图。`,
            timestamp: new Date().toLocaleTimeString(),
            targetTab: "walkthrough",
          },
        ]);
        return;
      }

      // 4. Theory mappings intent
      if (
        qLower.includes("理论") ||
        qLower.includes("论文") ||
        qLower.includes("映射") ||
        qLower.includes("公式") ||
        qLower.includes("theory") ||
        qLower.includes("mapping")
      ) {
        let currentWalk = walkthroughData;
        if (!currentWalk) {
          currentWalk = await api.getProjectWalkthrough(selectedProjectId);
          setWalkthroughData(currentWalk);
        }
        setWorkspaceTab("theory");
        const theoryCount = currentWalk?.theory_mappings?.length || 0;

        setCopilotMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            content: `已为您提取学术论文理论与源码符号映射图谱！\n\n- **映射项数**：共绑定 **${theoryCount}** 项学术形式化概念与工程符号\n\n已在中央视窗为您呈现【理论映射图谱】。`,
            timestamp: new Date().toLocaleTimeString(),
            targetTab: "theory",
          },
        ]);
        return;
      }

      // 5. Patch / Refactor intent
      if (
        qLower.startsWith("补丁") ||
        qLower.startsWith("重构") ||
        qLower.startsWith("修复") ||
        qLower.startsWith("优化代码") ||
        qLower.startsWith("patch") ||
        qLower.startsWith("refactor")
      ) {
        setCodingPrompt(q);
        const res = await api.proposeProjectCoding(selectedProjectId, q);
        setProposal(res);
        setWorkspaceTab("patch");

        setCopilotMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            content: `已根据您的需求生成代码治理补丁提案！\n\n**提案摘要**：\n${res.summary || "补丁已就绪"}\n\n已在中央视窗为您切换至【治理补丁提案】，可直接比对并一键应用至本地代码。`,
            timestamp: new Date().toLocaleTimeString(),
            targetTab: "patch",
          },
        ]);
        return;
      }

      // 6. General Q&A / Project exploration
      let answerText = "";
      let citedFiles: string[] = [];

      try {
        const askRes = await api.askProject(selectedProjectId, q);
        if (askRes && askRes.answer_markdown) {
          answerText = askRes.answer_markdown;
          citedFiles = askRes.cited_files || [];
        }
      } catch {
        // fallback to askProjectLearning
      }

      if (!answerText) {
        try {
          const learnAns = await api.askProjectLearning(selectedProjectId, q);
          if (learnAns && learnAns.answer) {
            answerText = learnAns.answer;
          }
        } catch {
          // ignore
        }
      }

      if (!answerText) {
        answerText = "已分析您的提问，但未能匹配到确切源码片段，请提供更具体的文件或类名。";
      }

      setCopilotMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: answerText,
          timestamp: new Date().toLocaleTimeString(),
          citedFiles,
        },
      ]);
    } catch (err: any) {
      setCopilotMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: `处理需求时遇到异常：${err.message || "未知错误，请重试"}`,
          timestamp: new Date().toLocaleTimeString(),
        },
      ]);
    } finally {
      setCopilotLoading(false);
    }
  };

  const selectedProj = projects.find((p) => p.project_id === selectedProjectId);

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* Top Project Bar */}
      <div className="shrink-0 flex flex-wrap items-center justify-between px-6 py-2.5 border-b border-border/70 bg-card/60 gap-3">
        <div className="flex items-center space-x-3">
          <FolderGit2 className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
          <select
            id="project-select"
            value={selectedProjectId}
            onChange={(e) => setSelectedProjectId(e.target.value)}
            className="text-xs sm:text-sm font-semibold bg-muted/60 border border-input rounded-md px-3 py-1 focus:outline-none focus:ring-1 focus:ring-ring max-w-xs truncate"
          >
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
          </select>

          <Button
            size="sm"
            variant="outline"
            onClick={() => setIsAddOpen(true)}
            className="h-7 px-2.5 text-xs font-serif-academic gap-1 border-emerald-800/40 text-emerald-800 dark:text-emerald-400 hover:bg-emerald-900/10"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>添加项目</span>
          </Button>

          <Button
            size="icon-sm"
            variant="ghost"
            title="刷新目录结构"
            onClick={() => {
              if (selectedProjectId) {
                setTreeLoading(true);
                api
                  .getProjectTree(selectedProjectId)
                  .then((res) => setTree(res.items || res.tree || []))
                  .finally(() => setTreeLoading(false));
              }
            }}
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", treeLoading && "animate-spin")} />
          </Button>
        </div>

        {/* Project Path & Git Info Badge */}
        {selectedProj && (
          <div className="flex items-center space-x-3 text-xs font-mono text-muted-foreground">
            <span
              className="hidden md:inline-block max-w-sm truncate text-muted-foreground/80"
              title={selectedProj.root_path || selectedProj.path}
            >
              路径: {selectedProj.root_path || selectedProj.path}
            </span>
            <div className="flex items-center space-x-1" id="proj-git-branch">
              <GitBranch className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
              <span>{selectedProj.branch || "main"}</span>
            </div>
            {selectedProj.dirty_files !== undefined && selectedProj.dirty_files > 0 && (
              <Badge variant="amber" className="text-[10px]">
                {selectedProj.dirty_files} 待提交变更
              </Badge>
            )}
          </div>
        )}
      </div>

      {/* Main Studio 3-Pane Layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Pane: Project File Tree */}
        <aside className="w-64 lg:w-72 shrink-0 border-r border-border/70 bg-card/20 flex flex-col h-full">
          <div className="p-3 border-b border-border/60 flex items-center justify-between">
            <span className="text-[11px] font-mono uppercase tracking-widest text-muted-foreground">
              Project Explorer
            </span>
            <span className="text-[11px] font-mono text-muted-foreground">
              {tree.length} 顶层项
            </span>
          </div>

          <div
            className="flex-1 overflow-y-auto p-2 space-y-0.5 text-xs font-mono"
            id="project-file-tree"
          >
            {treeLoading ? (
              <div className="p-6 text-muted-foreground text-center text-xs animate-pulse">
                正在加载目录结构...
              </div>
            ) : tree.length === 0 ? (
              <div className="p-6 text-muted-foreground text-center text-xs">
                该项目目录为空或未包含支持的代码文件
              </div>
            ) : (
              tree.map((item, idx) => (
                <FileTreeNode
                  key={item.path || idx}
                  node={item}
                  selectedFile={selectedFile}
                  onSelectFile={handleSelectFile}
                />
              ))
            )}
          </div>
        </aside>

        {/* Central Pane: Dynamic Multi-View Workspace */}
        <main className="flex-1 flex flex-col h-full overflow-hidden bg-background">
          {/* Workspace Tabs Header */}
          <div className="shrink-0 flex items-center justify-between px-3 py-1.5 border-b border-border/70 bg-card/40 gap-2 overflow-x-auto">
            <div className="flex items-center space-x-1.5 shrink-0">
              {([
                { id: "code", label: "📄 代码浏览", badge: undefined },
                { id: "audit", label: "🛡️ 架构体检报告", badge: auditData?.findings?.length },
                { id: "learning", label: "📖 导学剖析全景", badge: learningData?.progressive_reading_roadmap?.length },
                { id: "walkthrough", label: "📐 架构组件走查", badge: walkthroughData?.components?.length },
                { id: "theory", label: "🗺️ 理论映射图谱", badge: walkthroughData?.theory_mappings?.length },
                { id: "patch", label: "🛠️ 治理补丁提案", badge: proposal ? "1" : undefined },
                { id: "diff", label: "🔀 Git 差异", badge: diff ? "有差异" : undefined },
              ] as const).map((t) => (
                <button
                  key={t.id}
                  onClick={() => setWorkspaceTab(t.id)}
                  className={cn(
                    "px-3 py-1.5 rounded-md font-serif-academic text-xs transition cursor-pointer flex items-center gap-1.5 whitespace-nowrap",
                    workspaceTab === t.id
                      ? "bg-primary text-white font-semibold shadow-2xs"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  )}
                >
                  <span>{t.label}</span>
                  {t.badge !== undefined && (
                    <span
                      className={cn(
                        "text-[10px] px-1.5 py-0.2 rounded-full font-mono",
                        workspaceTab === t.id
                          ? "bg-white/20 text-white"
                          : "bg-muted text-muted-foreground"
                      )}
                    >
                      {t.badge}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Context Actions based on active workspace tab */}
            <div className="flex items-center space-x-2 shrink-0">
              {workspaceTab === "code" && selectedFile && (
                <>
                  {selectedFile.toLowerCase().endsWith(".md") && (
                    <div className="flex bg-muted/60 p-0.5 rounded border border-border/60 text-[11px]">
                      <button
                        type="button"
                        onClick={() => setViewMode("code")}
                        className={cn(
                          "px-2 py-0.5 rounded flex items-center space-x-1 transition font-serif-academic",
                          viewMode === "code"
                            ? "bg-background text-foreground shadow-2xs font-semibold"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                      >
                        <Code2 className="h-3 w-3" />
                        <span>代码</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setViewMode("preview")}
                        className={cn(
                          "px-2 py-0.5 rounded flex items-center space-x-1 transition font-serif-academic",
                          viewMode === "preview"
                            ? "bg-background text-foreground shadow-2xs font-semibold"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                      >
                        <Eye className="h-3 w-3" />
                        <span>渲染预览</span>
                      </button>
                    </div>
                  )}
                  {fileContent && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={handleCopyFile}
                      title="复制文件全部代码"
                      className="h-6 px-2 text-[11px] gap-1 font-serif-academic text-muted-foreground hover:text-foreground"
                    >
                      {copiedFile ? <Check className="h-3 w-3 text-emerald-600" /> : <Copy className="h-3 w-3" />}
                      <span>{copiedFile ? "已复制" : "复制代码"}</span>
                    </Button>
                  )}
                </>
              )}

              {(workspaceTab === "audit" || workspaceTab === "learning" || workspaceTab === "walkthrough" || workspaceTab === "theory") && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleRefreshGovernance}
                  disabled={govLoading || learningLoading}
                  className="h-7 px-2 text-xs font-serif-academic gap-1 text-muted-foreground hover:text-foreground"
                >
                  <RefreshCw className={cn("h-3 w-3", (govLoading || learningLoading) && "animate-spin")} />
                  <span>刷新分析</span>
                </Button>
              )}
            </div>
          </div>

          {/* Central Tab Content Area */}
          <div className="flex-1 overflow-auto">
            {/* VIEW 1: Code Viewer */}
            {workspaceTab === "code" && (
              <div className="h-full flex flex-col" id="proj-code-viewer">
                {selectedFile ? (
                  <>
                    <div className="p-2 border-b border-border/50 flex items-center justify-between text-xs text-muted-foreground font-mono bg-muted/10 shrink-0">
                      <div className="flex items-center space-x-2 truncate">
                        <span className="font-semibold text-foreground truncate max-w-md">
                          {selectedFile}
                        </span>
                        <Badge variant="outline" className="text-[10px] uppercase font-mono px-1.5 py-0">
                          {getLanguageFromPath(selectedFile)}
                        </Badge>
                        {fileContent && (
                          <span className="text-[10px] text-muted-foreground">
                            {fileContent.split("\n").length} 行 · {(fileContent.length / 1024).toFixed(1)} KB
                          </span>
                        )}
                      </div>

                      <div className="flex items-center space-x-2">
                        {selectedFile.toLowerCase().endsWith(".md") && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => setViewMode(viewMode === "code" ? "preview" : "code")}
                            className="h-7 px-2 text-xs font-serif-academic gap-1"
                          >
                            <Eye className="h-3.5 w-3.5" />
                            <span>{viewMode === "code" ? "渲染预览" : "源码"}</span>
                          </Button>
                        )}
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={handleCopyFile}
                          className="h-7 px-2 text-xs font-serif-academic gap-1"
                        >
                          {copiedFile ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
                          <span>{copiedFile ? "已复制" : "复制源码"}</span>
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => handleQuoteToCopilot(selectedSnippet || undefined)}
                          className={cn(
                            "h-7 px-2.5 text-xs font-serif-academic gap-1.5 transition cursor-pointer",
                            selectedSnippet
                              ? "border-emerald-600 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 font-semibold"
                              : "text-muted-foreground hover:text-foreground"
                          )}
                          title="选中代码片段后点击直接以 Markdown 引用格式填入右侧对话框"
                        >
                          <MessageSquare className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                          <span>
                            {selectedSnippet
                              ? `引用选中代码 [${calcLineRange(fileContent, selectedSnippet).startLine}-${calcLineRange(fileContent, selectedSnippet).endLine}行] 至对话`
                              : "引用当前文件至对话"}
                          </span>
                        </Button>
                      </div>
                    </div>
                    <div
                      className="flex-1 overflow-auto code-viewer-container relative"
                      onMouseUp={handleCodeMouseUp}
                      onScroll={() => {
                        setSelectionPos(null);
                        setSelectedSnippet("");
                      }}
                    >
                      {selectionPos && selectedSnippet && (
                        <div
                          style={{
                            position: "fixed",
                            left: `${selectionPos.x}px`,
                            top: `${selectionPos.y}px`,
                            transform: "translate(-50%, -100%)",
                            zIndex: 60,
                          }}
                          className="animate-in fade-in zoom-in-95 duration-150 shadow-xl pointer-events-auto"
                        >
                          <button
                            type="button"
                            onMouseDown={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              handleQuoteToCopilot(selectedSnippet);
                            }}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-emerald-800 hover:bg-emerald-900 text-white text-xs font-serif-academic shadow-md cursor-pointer transition select-none hover:scale-105 active:scale-95"
                          >
                            <MessageSquare className="h-3.5 w-3.5" />
                            <span>💬 引用至对话</span>
                          </button>
                        </div>
                      )}
                      {selectedFile.toLowerCase().endsWith(".md") && viewMode === "preview" ? (
                        <div
                          className="p-8 max-w-4xl mx-auto leading-relaxed text-sm font-serif-academic prose dark:prose-invert"
                          dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(fileContent) }}
                        />
                      ) : (
                        <div className="flex min-w-full font-mono text-xs leading-5">
                          <div className="select-none py-3 px-3 text-right code-viewer-gutter border-r shrink-0 font-mono text-[11px] leading-5">
                            {fileContent.split("\n").map((_, i) => (
                              <div key={i} className="h-5 leading-5">{i + 1}</div>
                            ))}
                          </div>
                          <pre
                            className="flex-1 py-3 px-4 overflow-x-auto m-0 bg-transparent font-mono text-[13px] leading-5 selection:bg-primary/20"
                            onMouseUp={handleCodeMouseUp}
                          >
                            <code dangerouslySetInnerHTML={{ __html: highlightCode(fileContent, getLanguageFromPath(selectedFile)) }} />
                          </pre>
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-center p-8 text-muted-foreground font-serif-academic">
                    <FileCode className="h-12 w-12 text-muted-foreground/40 mb-3" />
                    <p className="text-sm font-medium text-foreground/80 mb-1">请从左侧工程文件树中点击选择文件</p>
                    <p className="text-xs text-muted-foreground max-w-md">
                      支持语法高亮、行号对齐、公式与 Markdown 实时渲染，以及从右侧 AI 副驾驶直接跳转。
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* VIEW 2: Audit Report (架构体检报告) */}
            {workspaceTab === "audit" && (
              <div className="p-6 space-y-6 max-w-5xl mx-auto">
                {govLoading && !auditData ? (
                  <div className="space-y-3 py-16 text-center text-muted-foreground">
                    <RefreshCw className="h-8 w-8 animate-spin mx-auto text-emerald-700 dark:text-emerald-400" />
                    <p className="font-serif-academic text-sm">正在深度分析代码拓扑、契约接口与规范合规性...</p>
                  </div>
                ) : auditData ? (
                  <>
                    {/* Score Highlights */}
                    <div className="rounded-xl border border-border/80 bg-background/80 p-5 space-y-4 shadow-sm">
                      <div className="flex items-center justify-between">
                        <div>
                          <h3 className="font-serif-academic font-bold text-base text-foreground flex items-center gap-2">
                            <ShieldCheck className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />
                            架构健康与契约体检综合评估
                          </h3>
                          <p className="text-xs text-muted-foreground font-serif-academic mt-0.5">
                            基于 AST 结构、契约完备度与学术规范自动化审查
                          </p>
                        </div>
                        <Badge variant="outline" className="font-mono text-xs text-emerald-700 dark:text-emerald-300 border-emerald-600/30">
                          报告: {auditData.report_id?.slice(0, 10) || "Auto"}
                        </Badge>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-center">
                          <div className="text-2xl font-bold font-mono text-emerald-700 dark:text-emerald-300">
                            {auditData.health_score ?? 90}
                            <span className="text-xs font-normal text-muted-foreground">/100</span>
                          </div>
                          <div className="text-xs text-muted-foreground mt-1 font-serif-academic">架构健康度</div>
                        </div>

                        <div className="p-4 rounded-xl bg-blue-500/10 border border-blue-500/20 text-center">
                          <div className="text-2xl font-bold font-mono text-blue-700 dark:text-blue-300">
                            {auditData.implementation_score ?? 85}
                            <span className="text-xs font-normal text-muted-foreground">/100</span>
                          </div>
                          <div className="text-xs text-muted-foreground mt-1 font-serif-academic">契约实现度</div>
                        </div>

                        <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/20 text-center">
                          <div className="text-2xl font-bold font-mono text-amber-700 dark:text-amber-300">
                            {auditData.findings?.length || 0}
                          </div>
                          <div className="text-xs text-muted-foreground mt-1 font-serif-academic">治理发现项</div>
                        </div>

                        <div className="p-4 rounded-xl bg-purple-500/10 border border-purple-500/20 text-center">
                          <div className="text-2xl font-bold font-mono text-purple-700 dark:text-purple-300">
                            {Number(Object.values(auditData.status_counts || {}).reduce((a: number, b: any) => a + Number(b || 0), 0))}
                          </div>
                          <div className="text-xs text-muted-foreground mt-1 font-serif-academic">接口规范核查数</div>
                        </div>
                      </div>

                      {auditData.summary && (
                        <div
                          className="bg-muted/30 p-4 rounded-lg border border-border/50 font-serif-academic text-sm text-foreground/90 leading-relaxed prose dark:prose-invert max-w-none"
                          dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(auditData.summary) }}
                        />
                      )}
                    </div>

                    {/* Findings List */}
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <h4 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                          体检隐患与治理清单（{auditData.findings?.length || 0} 项）
                        </h4>
                        <span className="text-xs text-muted-foreground font-serif-academic">
                          点击文件即可在代码区精准定位
                        </span>
                      </div>

                      {auditData.findings && auditData.findings.length > 0 ? (
                        auditData.findings.map((f: any, idx: number) => {
                          const sev = f.severity || "info";
                          const sevColor =
                            sev === "critical"
                              ? "border-rose-600/40 bg-rose-50/40 dark:bg-rose-950/20 text-rose-800 dark:text-rose-300"
                              : sev === "high" || sev === "warning"
                              ? "border-amber-600/40 bg-amber-50/40 dark:bg-amber-950/20 text-amber-800 dark:text-amber-300"
                              : "border-blue-600/30 bg-blue-50/30 dark:bg-blue-950/20 text-blue-800 dark:text-blue-300";

                          return (
                            <div key={idx} className={cn("p-4 rounded-xl border space-y-2.5 transition shadow-2xs", sevColor)}>
                              <div className="flex items-start justify-between gap-3">
                                <div className="font-semibold text-sm leading-snug">
                                  {f.title}
                                </div>
                                <Badge variant="outline" className="text-xs font-mono shrink-0 uppercase">
                                  {sev}
                                </Badge>
                              </div>

                              <div
                                className="text-foreground/90 text-xs sm:text-sm leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                                dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(f.description) }}
                              />

                              {f.target_file && (
                                <button
                                  type="button"
                                  onClick={() => handleSelectFile(f.target_file)}
                                  className="text-xs font-mono text-emerald-700 dark:text-emerald-400 hover:underline flex items-center gap-1.5 text-left"
                                >
                                  <FileCode className="h-3.5 w-3.5 shrink-0" />
                                  <span>{f.target_file}{f.line_number ? `:${f.line_number}` : ""}</span>
                                </button>
                              )}

                              {f.snippet && (
                                <pre className="p-3 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-muted-foreground max-h-32">
                                  {f.snippet}
                                </pre>
                              )}

                              {f.recommendation && (
                                <div className="pt-2 border-t border-border/40 flex items-center justify-between gap-2">
                                  <span className="text-xs sm:text-sm text-muted-foreground font-serif-academic italic">
                                    💡 治理建议: {f.recommendation}
                                  </span>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="h-7 px-2.5 text-xs text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/10 gap-1 shrink-0 font-serif-academic"
                                    onClick={() => {
                                      setWorkspaceTab("patch");
                                      setCodingPrompt(`【架构治理修复】${f.title}
目标文件：${f.target_file}${f.line_number ? ` (第 ${f.line_number} 行)` : ""}
风险分类：${f.category || "规范治理"}（严重度：${f.severity}）
治理建议：${f.recommendation}`);
                                    }}
                                  >
                                    <span>带入补丁</span>
                                    <ArrowRight className="h-3 w-3" />
                                  </Button>
                                </div>
                              )}
                            </div>
                          );
                        })
                      ) : (
                        <div className="p-8 text-center text-muted-foreground rounded-xl border border-dashed border-border/80">
                          <CheckCircle2 className="h-8 w-8 mx-auto mb-2 text-emerald-600" />
                          <span className="font-serif-academic text-sm">架构体检全部达标，未发现违规或风险项</span>
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="p-12 text-center text-muted-foreground">
                    <AlertTriangle className="h-8 w-8 mx-auto mb-2 text-amber-500" />
                    <span className="font-serif-academic text-sm">暂无架构体检数据，请在右侧副驾驶输入“执行体检”或点击上方刷新。</span>
                  </div>
                )}
              </div>
            )}

            {/* VIEW 3: Learning Guide (导学剖析全景) */}
            {workspaceTab === "learning" && (
              <div className="p-6 space-y-6 max-w-5xl mx-auto">
                {learningLoading && !learningData ? (
                  <div className="space-y-3 py-16 text-center text-muted-foreground">
                    <RefreshCw className="h-8 w-8 animate-spin mx-auto text-emerald-700 dark:text-emerald-400" />
                    <p className="font-serif-academic text-sm">正在解构工程技术栈、使命目标与循序渐进路线图...</p>
                  </div>
                ) : learningData ? (
                  <>
                    {/* Mission & Ecosystem Card */}
                    <div className="rounded-xl border border-border/80 bg-background/80 p-5 space-y-4 shadow-sm">
                      <div className="flex items-center justify-between">
                        <span className="font-serif-academic font-bold text-foreground text-base flex items-center gap-2">
                          <Sparkles className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />
                          工程使命与技术栈全景
                        </span>
                        <Badge variant="outline" className="font-mono text-xs text-emerald-700 dark:text-emerald-300 border-emerald-600/30">
                          {learningData.ecosystem?.primary_stack || "Multi-Language"}
                        </Badge>
                      </div>
                      <p className="text-sm text-foreground/90 leading-relaxed font-serif-academic bg-muted/30 p-4 rounded-lg border border-border/40">
                        {learningData.mission?.purpose_and_value || "该项目提供模块化工程实现。"}
                      </p>
                      <div className="flex items-center gap-2 flex-wrap pt-1">
                        <span className="text-xs text-muted-foreground font-mono">生态标签:</span>
                        {(learningData.ecosystem?.languages || []).map((lang: string) => (
                          <Badge key={lang} variant="secondary" className="text-xs font-mono">
                            {lang}
                          </Badge>
                        ))}
                        {learningData.ecosystem?.package_manager && (
                          <Badge variant="outline" className="text-xs font-mono">
                            {learningData.ecosystem.package_manager}
                          </Badge>
                        )}
                      </div>
                    </div>

                    {/* Progressive Reading Roadmap */}
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-1.5">
                          <BookOpen className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                          循序渐进源码精读路线图 ({learningData.progressive_reading_roadmap?.length || 0} 阶段)
                        </span>
                        <span className="text-xs text-muted-foreground font-mono">
                          点击文件即可在左侧与中央视窗即时阅读
                        </span>
                      </div>

                      <div className="space-y-3">
                        {(learningData.progressive_reading_roadmap || []).map((step: any) => (
                          <div
                            key={step.step_number}
                            className="rounded-xl border border-border/70 bg-card/60 p-4 space-y-2.5 hover:border-border transition shadow-2xs"
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2.5">
                                <span className="h-6 w-6 rounded-full bg-emerald-800/10 text-emerald-800 dark:text-emerald-300 font-mono text-xs font-bold flex items-center justify-center">
                                  {step.step_number}
                                </span>
                                <span className="font-serif-academic font-bold text-foreground text-sm">
                                  [{step.stage}] {step.title}
                                </span>
                              </div>
                              <span className="text-xs font-mono px-2 py-0.5 rounded bg-muted text-muted-foreground">
                                {step.difficulty}
                              </span>
                            </div>
                            <p className="text-xs sm:text-sm text-muted-foreground leading-relaxed font-serif-academic">
                              {step.focus}
                            </p>
                            {step.tip && (
                              <div className="text-xs sm:text-sm text-emerald-800 dark:text-emerald-300/90 bg-emerald-950/10 dark:bg-emerald-950/30 p-3 rounded border border-emerald-800/20 font-serif-academic">
                                💡 <strong>精读建议：</strong>{step.tip}
                              </div>
                            )}
                            {step.files && step.files.length > 0 && (
                              <div className="pt-1 flex flex-wrap gap-2">
                                {step.files.map((file: string) => (
                                  <button
                                    key={file}
                                    onClick={() => handleSelectFile(file)}
                                    className={cn(
                                      "text-xs font-mono px-2.5 py-1 rounded-md border flex items-center gap-1.5 transition cursor-pointer",
                                      selectedFile === file
                                        ? "bg-primary text-white border-primary"
                                        : "bg-background/80 hover:bg-muted text-foreground border-border/70 hover:border-emerald-800/50"
                                    )}
                                    title="在代码查看器中打开"
                                  >
                                    <FileCode className="h-3.5 w-3.5 shrink-0 text-emerald-700 dark:text-emerald-400" />
                                    <span className="truncate max-w-[260px]">{file}</span>
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* AST Lexicon */}
                    {learningData.lexicon && learningData.lexicon.length > 0 && (
                      <div className="rounded-xl border border-border/80 bg-background/80 p-5 space-y-3 shadow-sm">
                        <div className="flex items-center justify-between">
                          <span className="font-serif-academic font-bold text-foreground text-sm flex items-center gap-1.5">
                            <Code2 className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                            核心类与关键符号索引 (Lexicon: {learningData.lexicon.length} 项)
                          </span>
                          <Badge variant="outline" className="font-mono text-xs">
                            AST 自动提取
                          </Badge>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-80 overflow-y-auto pr-1">
                          {learningData.lexicon.map((sym: any, idx: number) => (
                            <div
                              key={idx}
                              onClick={() => handleSelectFile(sym.file_path)}
                              className="p-3 rounded-lg border border-border/60 bg-muted/20 hover:bg-muted/60 hover:border-emerald-700/40 transition cursor-pointer flex flex-col gap-1"
                            >
                              <div className="flex items-center justify-between">
                                <span className="font-mono font-semibold text-xs text-emerald-800 dark:text-emerald-300 truncate">
                                  {sym.name}
                                </span>
                                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                                  {sym.kind}
                                </span>
                              </div>
                              <div className="text-[11px] text-muted-foreground font-mono truncate">
                                📄 {sym.file_path}:L{sym.line}
                              </div>
                              {sym.summary && (
                                <p className="text-xs text-foreground/80 font-serif-academic line-clamp-2">
                                  {sym.summary}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Vibecoding / Maturity Audit */}
                    <div className="rounded-xl border border-border/80 bg-background/80 p-5 space-y-3 shadow-sm">
                      <div className="flex items-center justify-between">
                        <span className="font-serif-academic font-bold text-foreground text-sm flex items-center gap-1.5">
                          <Scale className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                          Vibecoding / 原型卫生与演进评估
                        </span>
                        <Badge
                          variant="outline"
                          className={cn(
                            "font-mono text-xs font-bold",
                            (learningData.vibecoding_hygiene_audit?.maturity_score || 0) >= 80
                              ? "text-emerald-700 dark:text-emerald-300 border-emerald-600/30"
                              : "text-amber-700 dark:text-amber-300 border-amber-600/30"
                          )}
                        >
                          工程成熟度: {learningData.vibecoding_hygiene_audit?.maturity_score ?? 60}/100
                        </Badge>
                      </div>

                      {/* Strengths */}
                      {learningData.vibecoding_hygiene_audit?.strengths?.length > 0 && (
                        <div className="space-y-1.5">
                          <span className="text-xs font-serif-academic text-emerald-800 dark:text-emerald-300 font-medium flex items-center gap-1">
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            架构优势与合理设计
                          </span>
                          <ul className="text-xs sm:text-sm text-muted-foreground list-disc list-inside space-y-1 font-serif-academic">
                            {learningData.vibecoding_hygiene_audit.strengths.map((s: string, idx: number) => (
                              <li key={idx}>{s}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Risks */}
                      {learningData.vibecoding_hygiene_audit?.risks_and_anti_patterns?.length > 0 && (
                        <div className="space-y-1.5 pt-2 border-t border-border/40">
                          <span className="text-xs font-serif-academic text-rose-700 dark:text-rose-400 font-medium flex items-center gap-1">
                            <AlertTriangle className="h-3.5 w-3.5" />
                            原型常见隐患与技术债
                          </span>
                          <ul className="text-xs sm:text-sm text-muted-foreground list-disc list-inside space-y-1 font-serif-academic">
                            {learningData.vibecoding_hygiene_audit.risks_and_anti_patterns.map((r: string, idx: number) => (
                              <li key={idx} className="text-rose-900/80 dark:text-rose-300/80">{r}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Production Roadmap */}
                      {learningData.vibecoding_hygiene_audit?.production_roadmap?.length > 0 && (
                        <div className="space-y-1.5 pt-2 border-t border-border/40">
                          <span className="text-xs font-serif-academic text-foreground font-semibold flex items-center gap-1">
                            <ArrowRight className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                            走向生产级工程演进指南 (Evolution Checklist)
                          </span>
                          <div className="space-y-1 bg-muted/40 p-3 rounded-lg border border-border/50 font-serif-academic text-xs sm:text-sm text-foreground/90">
                            {learningData.vibecoding_hygiene_audit.production_roadmap.map((item: string, idx: number) => (
                              <p key={idx} className="leading-relaxed">{item}</p>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="text-center py-16 text-muted-foreground font-serif-academic text-sm">
                    暂未生成项目导学，可在右侧副驾驶输入“学习项目”或点击上方刷新生成。
                  </div>
                )}
              </div>
            )}

            {/* VIEW 4: Walkthrough & Topology (架构组件走查) */}
            {workspaceTab === "walkthrough" && (
              <div className="p-6 space-y-6 max-w-5xl mx-auto">
                {walkthroughData ? (
                  <>
                    {walkthroughData.overview && (
                      <div className="p-4 rounded-xl border border-border/80 bg-background/90 space-y-2 shadow-2xs">
                        <span className="font-serif-academic font-bold text-sm text-foreground">架构概览</span>
                        <div
                          className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                          dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(walkthroughData.overview) }}
                        />
                      </div>
                    )}

                    {walkthroughData.data_flow_description && (
                      <div className="p-4 rounded-xl border border-border/80 bg-background/90 space-y-2 shadow-2xs">
                        <span className="font-serif-academic font-bold text-sm text-foreground">数据流与交互闭环</span>
                        <div
                          className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                          dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(walkthroughData.data_flow_description) }}
                        />
                      </div>
                    )}

                    {walkthroughData.components && walkthroughData.components.length > 0 && (
                      <div className="space-y-3">
                        <span className="font-serif-academic font-bold text-sm text-foreground">分层核心组件</span>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                          {walkthroughData.components.map((comp: any, idx: number) => (
                            <div key={idx} className="p-3.5 rounded-xl border border-border/70 bg-card space-y-2 shadow-2xs">
                              <div className="flex items-center justify-between">
                                <span className="font-bold text-xs sm:text-sm text-foreground">{comp.name}</span>
                                <Badge variant="outline" className="text-xs font-mono">
                                  {comp.layer}
                                </Badge>
                              </div>
                              <div
                                className="text-xs text-muted-foreground font-serif-academic prose dark:prose-invert max-w-none"
                                dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(comp.responsibilities) }}
                              />
                              {comp.files && comp.files.length > 0 && (
                                <div className="flex flex-wrap gap-1.5 pt-1">
                                  {comp.files.map((file: string, fIdx: number) => (
                                    <button
                                      key={fIdx}
                                      type="button"
                                      onClick={() => handleSelectFile(file)}
                                      className="text-xs font-mono px-2 py-0.5 rounded bg-muted/60 text-muted-foreground hover:text-foreground cursor-pointer"
                                    >
                                      {file.split("/").pop()}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {walkthroughData.mermaid_topology && (
                      <div className="p-4 rounded-xl border border-border/80 bg-background/90 space-y-2 shadow-2xs">
                        <span className="font-serif-academic font-bold text-sm text-foreground">架构拓扑定义 (Mermaid)</span>
                        <pre className="p-3 rounded-lg bg-muted/60 text-xs font-mono overflow-x-auto text-muted-foreground">
                          {walkthroughData.mermaid_topology}
                        </pre>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="p-16 text-center text-muted-foreground">
                    <RefreshCw className="h-8 w-8 animate-spin mx-auto text-muted-foreground mb-3" />
                    <span className="font-serif-academic text-sm">正在分析架构拓扑与分层走查...</span>
                  </div>
                )}
              </div>
            )}

            {/* VIEW 5: Theory Mappings (理论映射图谱) */}
            {workspaceTab === "theory" && (
              <div className="p-6 space-y-6 max-w-5xl mx-auto">
                <div className="rounded-xl bg-muted/30 border border-border/60 p-4">
                  <p className="text-xs sm:text-sm text-muted-foreground font-serif-academic leading-relaxed">
                    理论-代码映射矩阵旨在建立学术论文中的形式化定义、公理或算法原型与本工程源码符号之间的严格追踪链。
                  </p>
                </div>

                {walkthroughData?.theory_mappings && walkthroughData.theory_mappings.length > 0 ? (
                  <div className="space-y-4">
                    {walkthroughData.theory_mappings.map((tm: any, idx: number) => (
                      <div key={idx} className="p-4 rounded-xl border border-border/80 bg-background/90 space-y-3 shadow-2xs">
                        <div className="flex items-center justify-between">
                          <span className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-2">
                            <span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-600" />
                            {tm.concept}
                          </span>
                          {tm.paper_reference && (
                            <Badge variant="outline" className="text-xs font-mono text-muted-foreground">
                              {tm.paper_reference}
                            </Badge>
                          )}
                        </div>

                        {tm.description && (
                          <div
                            className="text-xs sm:text-sm text-foreground/90 leading-relaxed font-serif-academic prose dark:prose-invert max-w-none"
                            dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(tm.description) }}
                          />
                        )}

                        <div className="bg-muted/40 p-3 rounded-lg font-mono text-xs space-y-1.5">
                          <div className="flex items-center justify-between text-muted-foreground">
                            <span>源码符号:</span>
                            <span className="text-emerald-700 dark:text-emerald-300 font-semibold">{tm.code_symbol}</span>
                          </div>
                          {tm.file_path && (
                            <div className="flex items-center justify-between pt-1.5 border-t border-border/40">
                              <span>实现文件:</span>
                              <button
                                type="button"
                                onClick={() => handleSelectFile(tm.file_path)}
                                className="text-emerald-700 dark:text-emerald-400 hover:underline truncate max-w-md cursor-pointer"
                              >
                                {tm.file_path}{tm.line_number ? `:${tm.line_number}` : ""}
                              </button>
                            </div>
                          )}
                        </div>

                        {tm.design_rationale && (
                          <div className="text-xs text-muted-foreground font-serif-academic italic">
                            💡 设计权衡：{tm.design_rationale}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-16 text-center text-muted-foreground rounded-xl border border-dashed border-border/80">
                    <Info className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
                    <span className="font-serif-academic text-sm">暂未提取到理论映射项，请在副驾驶描述“理论映射”或刷新全景走查。</span>
                  </div>
                )}
              </div>
            )}

            {/* VIEW 6: Patch Proposal (治理补丁提案) */}
            {workspaceTab === "patch" && (
              <div className="p-6 space-y-6 max-w-4xl mx-auto">
                <div className="rounded-xl border border-border/80 bg-background/90 p-5 space-y-4 shadow-sm">
                  <div>
                    <h3 className="font-serif-academic font-bold text-base text-foreground flex items-center gap-2">
                      <Wand2 className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />
                      代码治理与重构补丁生成器
                    </h3>
                    <p className="text-xs text-muted-foreground font-serif-academic mt-0.5">
                      输入重构意图，智能体将自动化生成语义补丁并提供差异预览
                    </p>
                  </div>

                  {/* Presets */}
                  <div className="space-y-1.5">
                    <span className="text-muted-foreground text-xs font-serif-academic">常用治理意图预设：</span>
                    <div className="flex flex-wrap gap-2">
                      {[
                        "补全异常防御与类型校验",
                        "对齐学术论文符号与注释",
                        "提取核心契约并解耦模块",
                        "生成关键模块单元测试",
                      ].map((preset, i) => (
                        <button
                          key={i}
                          type="button"
                          onClick={() => setCodingPrompt(preset)}
                          className="text-xs font-serif-academic px-2.5 py-1 rounded-md bg-muted/50 hover:bg-muted text-foreground border border-border/60 transition cursor-pointer"
                        >
                          {preset}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <label className="text-muted-foreground font-medium text-xs">
                      重构 / 补丁治理意图说明
                    </label>
                    <textarea
                      rows={5}
                      value={codingPrompt}
                      onChange={(e) => setCodingPrompt(e.target.value)}
                      placeholder="例如：重构数据验证模块，添加异常处理与边界单元测试..."
                      className="w-full text-xs sm:text-sm p-3 rounded-lg border border-input bg-transparent focus:outline-none focus:ring-1 focus:ring-ring font-mono leading-relaxed"
                    />
                    <Button
                      id="proj-coding-propose-btn"
                      size="sm"
                      className="w-full gap-2 mt-1 bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic cursor-pointer h-9"
                      disabled={loading || !codingPrompt.trim()}
                      onClick={() => handleProposeCoding()}
                    >
                      <Play className="h-4 w-4" />
                      <span>{loading ? "分析并生成治理补丁中..." : "生成代码治理提案"}</span>
                    </Button>
                  </div>
                </div>

                {proposal && (
                  <div className="p-5 rounded-xl border border-emerald-500/40 bg-emerald-500/5 space-y-4 shadow-sm">
                    <div className="flex items-center justify-between font-medium text-emerald-700 dark:text-emerald-400">
                      <span className="font-mono text-xs sm:text-sm">提案 ID: {proposal.proposal_id}</span>
                      <Badge variant="outline" className="text-xs font-mono">就绪</Badge>
                    </div>
                    <div
                      className="text-foreground/90 text-xs sm:text-sm leading-relaxed font-serif-academic bg-background/80 p-3.5 rounded-lg border border-border/50 prose dark:prose-invert max-w-none"
                      dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(proposal.summary || "已生成语义补丁方案") }}
                    />
                    <Button
                      id="proj-prop-apply-btn"
                      size="default"
                      variant="default"
                      className="w-full gap-2 bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic cursor-pointer"
                      onClick={handleApplyCoding}
                    >
                      <Check className="h-4 w-4" />
                      <span>应用此补丁至工作区</span>
                    </Button>
                  </div>
                )}
              </div>
            )}

            {/* VIEW 7: Git Diff (Git 差异追踪) */}
            {workspaceTab === "diff" && (
              <div className="h-full flex flex-col p-6 max-w-5xl mx-auto space-y-4 overflow-y-auto">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-3.5">
                  <div className="flex items-center gap-2.5">
                    <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
                      <GitCompare className="h-4 w-4" />
                    </div>
                    <div>
                      <h3 className="font-serif-academic font-bold text-sm text-foreground flex items-center gap-2">
                        工作区 Git 语义差异追踪
                      </h3>
                      <p className="text-[11px] text-muted-foreground font-serif-academic">
                        实时监控工作区未提交修改（暂存/未暂存/新文件）及与基准分支的代码演进
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <div className="flex items-center space-x-1.5 bg-muted/40 px-2.5 py-1 rounded-md border border-border/60 text-xs font-mono">
                      <GitBranch className="h-3 w-3 text-muted-foreground" />
                      <span className="text-muted-foreground">当前:</span>
                      <span className="font-semibold text-emerald-700 dark:text-emerald-400">
                        {semanticDiff?.current_branch || "HEAD"}
                      </span>
                      <span className="text-muted-foreground">⟷ 对比:</span>
                      <input
                        value={compareBranch}
                        onChange={(e) => setCompareBranch(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            fetchSemanticDiff(compareBranch);
                          }
                        }}
                        className="w-16 h-5 px-1 bg-background border border-border/80 rounded text-[11px] font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                        title="输入对比基准分支名称后按回车刷新"
                      />
                    </div>

                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => fetchSemanticDiff(compareBranch)}
                      disabled={diffLoading}
                      className="h-7 px-2.5 text-xs font-serif-academic gap-1.5 cursor-pointer"
                    >
                      <RefreshCw className={cn("h-3 w-3", diffLoading && "animate-spin")} />
                      <span>刷新对比</span>
                    </Button>
                  </div>
                </div>

                {/* Stats & Intent Card */}
                {semanticDiff && (
                  <div className="p-4 rounded-xl border border-border/70 bg-card/50 space-y-3 shadow-2xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className="text-xs font-mono bg-background">
                          {semanticDiff.impact_level === "major_experiment"
                            ? "🚀 重大实验变动"
                            : semanticDiff.impact_level === "refactor"
                            ? "🔨 结构性重构"
                            : semanticDiff.impact_level === "ui_enhancement"
                            ? "🎨 界面交互优化"
                            : semanticDiff.impact_level === "test_suite"
                            ? "🧪 测试套件增强"
                            : semanticDiff.impact_level === "docs_only"
                            ? "📝 文档与配置"
                            : "✨ 常规代码微调"}
                        </Badge>
                        {semanticDiff.changed_areas && semanticDiff.changed_areas.map((a: string) => (
                          <span
                            key={a}
                            className="px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/10 text-emerald-800 dark:text-emerald-300 border border-emerald-500/20 font-serif-academic"
                          >
                            {a === "core_algorithms" ? "核心算法与智能体" :
                             a === "api_service" ? "API服务与契约" :
                             a === "storage_data" ? "数据存储与持久化" :
                             a === "workbench_ui" ? "前端工作台与交互" :
                             a === "test_verification" ? "自动化测试套件" :
                             a === "documentation_config" ? "文档与工程配置" : a}
                          </span>
                        ))}
                      </div>

                      <div className="flex items-center space-x-2 text-xs font-mono">
                        <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 font-semibold border border-emerald-500/20">
                          +{semanticDiff.total_additions || 0} 行
                        </span>
                        <span className="px-2 py-0.5 rounded bg-rose-500/10 text-rose-700 dark:text-rose-400 font-semibold border border-rose-500/20">
                          -{semanticDiff.total_deletions || 0} 行
                        </span>
                        <span className="text-muted-foreground text-[11px]">
                          共 {semanticDiff.file_diff_summaries?.length || 0} 个变更项
                        </span>
                      </div>
                    </div>

                    <div className="text-xs text-foreground/90 font-serif-academic leading-relaxed bg-background/60 p-2.5 rounded-lg border border-border/50">
                      {semanticDiff.experiment_intent}
                    </div>
                  </div>
                )}

                {/* Changed Files Summary List */}
                {semanticDiff?.file_diff_summaries && semanticDiff.file_diff_summaries.length > 0 && (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs font-serif-academic text-muted-foreground px-1">
                      <span>变更文件清单（点击可直接打开源码查看）</span>
                      <span>状态 · 增删行统计</span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {semanticDiff.file_diff_summaries.map((f: any, idx: number) => (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => handleSelectFile(f.file)}
                          className="flex items-center justify-between p-2 rounded-lg border border-border/60 bg-background hover:bg-muted/40 transition text-left text-xs font-mono group cursor-pointer"
                        >
                          <div className="flex items-center space-x-2 truncate mr-2">
                            <FileCode className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400 shrink-0 group-hover:scale-110 transition" />
                            <span className="truncate text-foreground/90 group-hover:text-emerald-700 dark:group-hover:text-emerald-400 font-medium">
                              {f.file}
                            </span>
                          </div>
                          <div className="flex items-center space-x-1.5 shrink-0">
                            <span
                              className={cn(
                                "px-1.5 py-0.2 rounded text-[10px] uppercase font-mono",
                                f.status === "worktree_modified"
                                  ? "bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/30"
                                  : f.status === "untracked"
                                  ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/30"
                                  : "bg-sky-500/15 text-sky-700 dark:text-sky-400 border border-sky-500/30"
                              )}
                            >
                              {f.status === "worktree_modified"
                                ? "未提交"
                                : f.status === "untracked"
                                ? "未跟踪"
                                : "分支提交"}
                            </span>
                            {(f.additions > 0 || f.deletions > 0) && (
                              <span className="text-[10px] text-muted-foreground">
                                <span className="text-emerald-600">+{f.additions}</span>
                                {" / "}
                                <span className="text-rose-600">-{f.deletions}</span>
                              </span>
                            )}
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Unified Diff View */}
                {diff ? (
                  <div className="space-y-1.5 flex-1 flex flex-col min-h-0">
                    <div className="flex items-center justify-between text-xs font-serif-academic text-muted-foreground px-1">
                      <span>统一补丁差异 (Unified Diff)</span>
                      <span className="font-mono text-[11px]">{diff.split("\n").length} 行补丁数据</span>
                    </div>
                    <pre className="flex-1 p-4 rounded-xl border border-border/70 bg-muted/25 font-mono text-xs overflow-auto leading-relaxed text-foreground/90 selection:bg-primary/20 max-h-[520px]">
                      <code>
                        {diff.split("\n").map((line: string, i: number) => {
                          const isAdd = line.startsWith("+") && !line.startsWith("+++");
                          const isDel = line.startsWith("-") && !line.startsWith("---");
                          const isHunk = line.startsWith("@@");
                          const isHeader = line.startsWith("diff --git") || line.startsWith("# ===");
                          return (
                            <div
                              key={i}
                              className={cn(
                                "px-1 -mx-1",
                                isAdd && "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 font-medium",
                                isDel && "bg-rose-500/15 text-rose-700 dark:text-rose-400 font-medium",
                                isHunk && "text-sky-600 dark:text-sky-400 font-bold bg-sky-500/5",
                                isHeader && "text-foreground font-bold border-t border-border/40 pt-1 mt-1"
                              )}
                            >
                              {line || " "}
                            </div>
                          );
                        })}
                      </code>
                    </pre>
                  </div>
                ) : (
                  (!semanticDiff?.file_diff_summaries || semanticDiff.file_diff_summaries.length === 0) && (
                    <div className="p-16 text-center text-muted-foreground rounded-xl border border-dashed border-border/80">
                      <CheckCircle2 className="h-8 w-8 mx-auto mb-2 text-emerald-600" />
                      <span className="font-serif-academic text-sm block">工作区当前为整洁状态，无未提交的代码变更。</span>
                      <span className="font-serif-academic text-xs text-muted-foreground mt-1 block">
                        当前分支与对比基准（{compareBranch}）亦完全一致。
                      </span>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => fetchSemanticDiff(compareBranch)}
                        className="mt-3 h-7 text-xs font-serif-academic"
                      >
                        重新检测
                      </Button>
                    </div>
                  )
                )}
              </div>
            )}
          </div>
        </main>

        {/* Right Pane: AI Project Copilot (智能项目副驾驶对话框) */}
        <aside className="w-80 lg:w-96 xl:w-[420px] shrink-0 border-l border-border/70 bg-card/30 flex flex-col h-full overflow-hidden">
          {/* Copilot Header */}
          <div className="p-3 border-b border-border/60 bg-card/60 flex items-center justify-between shrink-0">
            <div className="flex items-center space-x-2 text-foreground font-serif-academic font-bold text-xs sm:text-sm">
              <Bot className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
              <span>AI 项目副驾驶</span>
            </div>
            <div className="flex items-center space-x-1">
              {copilotMessages.length > 0 && (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  title="清空副驾驶对话"
                  onClick={() => setCopilotMessages([])}
                  className="h-7 w-7 text-muted-foreground hover:text-foreground"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
              <Button
                size="icon-sm"
                variant="ghost"
                title="重新执行全量架构与契约体检"
                onClick={handleRefreshGovernance}
                className="h-7 w-7 text-muted-foreground hover:text-foreground"
              >
                <RefreshCw className={cn("h-3.5 w-3.5", (govLoading || learningLoading) && "animate-spin")} />
              </Button>
            </div>
          </div>

          {/* Copilot Message Stream */}
          <div className="flex-1 overflow-y-auto p-3.5 space-y-3.5 text-xs sm:text-sm">
            {copilotMessages.length === 0 ? (
              <div className="p-4 rounded-xl border border-border/70 bg-muted/30 space-y-3 font-serif-academic">
                <div className="flex items-center gap-2 text-emerald-800 dark:text-emerald-300 font-bold text-sm">
                  <Sparkles className="h-4 w-4 shrink-0" />
                  <span>您好！我是您的项目工程 Copilot</span>
                </div>
                <p className="text-xs sm:text-sm text-foreground/80 leading-relaxed">
                  您可以向我自由描述任何需求，无论是<strong>项目快速学习、架构与代码体检、拓扑走查、论文理论对齐，还是生成修复补丁与任意源码提问</strong>。
                </p>
                <div className="pt-2 border-t border-border/40 text-xs text-muted-foreground space-y-1.5">
                  <p className="font-semibold text-foreground/90">推荐体验指令：</p>
                  <div className="flex flex-col gap-1">
                    <button
                      type="button"
                      onClick={() => handleSendCopilot("帮我做一次项目体检，查找架构隐患与规范问题")}
                      className="text-left text-xs p-1.5 rounded hover:bg-muted text-emerald-700 dark:text-emerald-400 cursor-pointer"
                    >
                      🛡️ “帮我做一次项目体检，查找架构隐患与规范问题”
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSendCopilot("我想深入学习这个项目，请给出导学精读路线图")}
                      className="text-left text-xs p-1.5 rounded hover:bg-muted text-emerald-700 dark:text-emerald-400 cursor-pointer"
                    >
                      📖 “我想深入学习这个项目，请给出导学精读路线图”
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSendCopilot("走查核心组件与数据流架构")}
                      className="text-left text-xs p-1.5 rounded hover:bg-muted text-emerald-700 dark:text-emerald-400 cursor-pointer"
                    >
                      📐 “走查核心组件与数据流架构”
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              copilotMessages.map((msg) => (
                <div
                  key={msg.id}
                  className={cn(
                    "flex flex-col space-y-1.5 w-full min-w-0",
                    msg.role === "user" ? "items-end" : "items-start"
                  )}
                >
                  <div className="flex items-center space-x-1.5 text-[11px] text-muted-foreground px-1">
                    {msg.role === "user" ? (
                      <>
                        <span>您</span>
                        <User className="h-3 w-3" />
                      </>
                    ) : (
                      <>
                        <Bot className="h-3 w-3 text-emerald-700 dark:text-emerald-400" />
                        <span>项目 Copilot</span>
                      </>
                    )}
                    <span>· {msg.timestamp}</span>
                  </div>

                  <div
                    className={cn(
                      "p-3 rounded-2xl text-xs sm:text-sm leading-relaxed min-w-0 overflow-hidden",
                      msg.role === "user"
                        ? "bg-emerald-800 text-white font-serif-academic rounded-tr-xs shadow-2xs max-w-[90%]"
                        : "w-full max-w-full bg-background border border-border/80 text-foreground font-serif-academic rounded-tl-xs shadow-2xs"
                    )}
                  >
                    {msg.role === "user" ? (
                      <div className="whitespace-pre-wrap">{msg.content}</div>
                    ) : (
                      <div
                        className="prose dark:prose-invert max-w-none text-xs sm:text-sm leading-relaxed overflow-x-auto"
                        dangerouslySetInnerHTML={{
                          __html: renderMarkdownWithMath(msg.content),
                        }}
                      />
                    )}

                    {msg.targetTab && (
                      <div className="pt-2 mt-2 border-t border-border/40 flex justify-end">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setWorkspaceTab(msg.targetTab!)}
                          className="h-6 px-2 text-[11px] font-serif-academic gap-1 text-emerald-800 dark:text-emerald-300"
                        >
                          <span>在中央视窗查看</span>
                          <ArrowRight className="h-3 w-3" />
                        </Button>
                      </div>
                    )}

                    {msg.citedFiles && msg.citedFiles.length > 0 && (
                      <div className="pt-2 mt-2 border-t border-border/40 flex flex-wrap gap-1.5">
                        <span className="text-[11px] text-muted-foreground font-mono">定位引用:</span>
                        {msg.citedFiles.map((file: string) => (
                          <button
                            key={file}
                            onClick={() => handleSelectFile(file)}
                            className="text-[11px] font-mono px-2 py-0.5 rounded border border-border/70 hover:bg-muted text-foreground cursor-pointer flex items-center gap-1"
                          >
                            <FileCode className="h-3 w-3 text-emerald-700 dark:text-emerald-400" />
                            <span>{file}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}

            {copilotLoading && (
              <div className="flex items-center space-x-2 text-xs text-muted-foreground p-3 rounded-xl bg-background border border-border/60">
                <RefreshCw className="h-3.5 w-3.5 animate-spin text-emerald-700 dark:text-emerald-400" />
                <span className="font-serif-academic">Copilot 正在分析项目拓扑、研读源码与执行逻辑...</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Quick Capability Chips */}
          <div className="px-3 pt-2 pb-1 border-t border-border/50 bg-card/40 flex items-center gap-1.5 overflow-x-auto text-[11px] shrink-0">
            {[
              { label: "🛡️ 架构体检", query: "帮我做一次项目架构与规范体检" },
              { label: "📖 导学精读", query: "生成循序渐进源码精读路线图" },
              { label: "📐 架构走查", query: "走查核心组件与数据流" },
              { label: "🗺️ 理论映射", query: "提取学术理论与代码符号映射" },
              { label: "🛠️ 补丁提案", query: "补全异常防御与边界单元测试" },
            ].map((chip, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => handleSendCopilot(chip.query)}
                className="px-2.5 py-1 rounded-full border border-border/70 bg-background/80 hover:bg-muted text-foreground/80 hover:text-foreground transition whitespace-nowrap cursor-pointer font-serif-academic"
              >
                {chip.label}
              </button>
            ))}
          </div>

          {/* Suggested Questions from Learning data */}
          {learningData?.suggested_exploration_questions && learningData.suggested_exploration_questions.length > 0 && (
            <div className="px-3 py-1 bg-card/20 flex items-center gap-1.5 overflow-x-auto text-[11px] shrink-0">
              <span className="text-muted-foreground font-serif-academic shrink-0">猜你想问:</span>
              {learningData.suggested_exploration_questions.slice(0, 3).map((q: string, idx: number) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => handleSendCopilot(q)}
                  className="px-2 py-0.5 rounded bg-muted/50 hover:bg-muted text-foreground/80 hover:text-foreground transition truncate max-w-[200px] cursor-pointer font-serif-academic"
                  title={q}
                >
                  {q}
                </button>
              ))}
            </div>
          )}

          {/* Copilot Input Box */}
          <div className="p-3 border-t border-border/60 bg-card/60 shrink-0">
            <div className="flex items-end gap-2 bg-background rounded-xl border border-input p-2 focus-within:ring-1 focus-within:ring-ring">
              <textarea
                ref={copilotTextareaRef}
                rows={2}
                value={copilotInput}
                onChange={(e) => setCopilotInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSendCopilot();
                  }
                }}
                placeholder="向 Copilot 描述需求（如学习/体检/架构走查/生成补丁/源码疑问）..."
                className="flex-1 bg-transparent text-xs sm:text-sm placeholder:text-muted-foreground focus:outline-none resize-none font-serif-academic leading-relaxed"
              />
              <Button
                size="icon-sm"
                disabled={copilotLoading || !copilotInput.trim()}
                onClick={() => handleSendCopilot()}
                className="h-8 w-8 bg-emerald-800 hover:bg-emerald-900 text-white rounded-lg shrink-0 cursor-pointer"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </aside>
      </div>

      {/* Add Project Modal Dialog */}
      <Dialog open={isAddOpen} onOpenChange={setIsAddOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <div className="flex items-center space-x-2 text-xs font-mono text-emerald-700 dark:text-emerald-400">
              <FolderGit2 className="h-3.5 w-3.5" />
              <span>Project Registration</span>
            </div>
            <DialogTitle className="text-base font-serif-academic font-bold">
              接入本地工程项目
            </DialogTitle>
            <DialogDescription className="text-xs">
              支持直接打开系统资源管理器选择本地文件夹，亦可同时添加多个目录（应对前后端分离或多分支仓库工程）。
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleCreateProject} className="space-y-4 pt-2 text-xs">
            <div className="space-y-1.5">
              <label className="font-medium text-foreground">
                项目名称 <span className="text-rose-500">*</span>
              </label>
              <input
                required
                placeholder="例如：My-Agent-System 或 Fullstack-Project"
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
                className="w-full h-9 rounded-md border border-input bg-transparent px-3 py-1 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>

            {/* Folder Selection Area */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="font-medium text-foreground">
                  工程根目录列表 <span className="text-rose-500">*</span>
                </label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleBrowseFolder}
                  disabled={browsingFolder}
                  className="h-7 px-2.5 text-xs font-serif-academic gap-1.5 border-emerald-700/40 text-emerald-800 dark:text-emerald-300 hover:bg-emerald-900/10"
                >
                  <FolderSearch className="h-3.5 w-3.5" />
                  <span>{browsingFolder ? "正在打开资源管理器..." : "打开文件资源管理器选择"}</span>
                </Button>
              </div>

              {/* Selected Folders Chips/List */}
              {addPaths.length > 0 ? (
                <div className="space-y-1.5 p-2 rounded-lg border border-border/70 bg-muted/20">
                  {addPaths.map((p, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between bg-card px-2.5 py-1.5 rounded border border-border/50 text-xs font-mono group"
                    >
                      <div className="flex items-center space-x-1.5 truncate">
                        <Folder className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-500" />
                        <span className="truncate text-foreground/90 font-medium">{p}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRemovePath(idx)}
                        className="text-muted-foreground hover:text-rose-500 p-0.5 rounded transition shrink-0 ml-2 cursor-pointer"
                        title="移除该路径"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-3 rounded-lg border border-dashed border-border/80 text-center text-muted-foreground text-xs font-serif-academic">
                  暂未选择工程文件夹。请点击上方【打开文件资源管理器选择】按钮，或在下方手动输入绝对路径。
                </div>
              )}

              {/* Manual Input Fallback */}
              <div className="flex items-center space-x-2 pt-1">
                <input
                  placeholder="手动输入或粘贴绝对路径（例如：D:\code\my-app）..."
                  value={manualPathInput}
                  onChange={(e) => setManualPathInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddManualPath();
                    }
                  }}
                  className="flex-1 h-8 rounded-md border border-input bg-transparent px-3 py-1 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={handleAddManualPath}
                  disabled={!manualPathInput.trim()}
                  className="h-8 px-3 text-xs font-serif-academic shrink-0 cursor-pointer"
                >
                  添加路径
                </Button>
              </div>

              {/* Expandable In-Modal Local Directory Quick Browser */}
              <div className="pt-1">
                <button
                  type="button"
                  onClick={toggleFsBrowser}
                  className="flex items-center space-x-1.5 text-xs text-emerald-800 dark:text-emerald-300 hover:underline cursor-pointer font-serif-academic"
                >
                  <Folder className="h-3.5 w-3.5" />
                  <span>{showFsBrowser ? "收起本地磁盘与目录点选浏览器 ▲" : "或：展开本地磁盘与目录浏览器 (网页内直接点选) ▼"}</span>
                </button>

                {showFsBrowser && (
                  <div className="mt-2 p-3 rounded-lg border border-border/80 bg-muted/30 space-y-2.5">
                    {/* Drives and Quick Roots */}
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[11px] text-muted-foreground font-mono">驱动器/快速路径:</span>
                      {fsDrives.map((d) => (
                        <button
                          key={d}
                          type="button"
                          onClick={() => navigateFsDir(d)}
                          className={cn(
                            "px-2 py-0.5 rounded text-[11px] font-mono border transition cursor-pointer",
                            currentFsPath.startsWith(d)
                              ? "bg-emerald-800 text-white border-emerald-800 font-semibold"
                              : "bg-background border-border/70 hover:bg-muted text-foreground"
                          )}
                        >
                          {d}
                        </button>
                      ))}
                      {fsQuickRoots.map((qr) => (
                        <button
                          key={qr}
                          type="button"
                          onClick={() => navigateFsDir(qr)}
                          className="px-2 py-0.5 rounded text-[11px] font-mono border bg-background border-border/70 hover:bg-muted text-foreground truncate max-w-[140px] cursor-pointer"
                          title={qr}
                        >
                          {qr.split(/[/\\]/).filter(Boolean).slice(-1)[0] || qr}
                        </button>
                      ))}
                    </div>

                    {/* Current Path Bar with Back button and Select button */}
                    <div className="flex items-center justify-between gap-2 p-1.5 rounded bg-background border border-border/60 text-xs font-mono">
                      <div className="flex items-center space-x-1.5 truncate">
                        {currentFsParent && (
                          <button
                            type="button"
                            onClick={() => navigateFsDir(currentFsParent)}
                            className="p-1 hover:bg-muted rounded text-foreground/80 hover:text-foreground shrink-0"
                            title="返回上一级目录"
                          >
                            ⬅
                          </button>
                        )}
                        <span className="truncate text-foreground/90 font-medium">{currentFsPath || "请选择路径..."}</span>
                      </div>
                      {currentFsPath && (
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => handleSelectFsDir(currentFsPath)}
                          className="h-6 px-2 text-[11px] bg-emerald-800 hover:bg-emerald-900 text-white shrink-0 cursor-pointer"
                        >
                          ➕ 选用当前目录
                        </Button>
                      )}
                    </div>

                    {/* Subdirectories List */}
                    <div className="max-h-44 overflow-y-auto space-y-1 rounded border border-border/40 bg-background/80 p-1.5 text-xs font-mono">
                      {fsBrowserLoading ? (
                        <div className="p-3 text-center text-muted-foreground text-xs font-serif-academic flex items-center justify-center gap-2">
                          <RefreshCw className="h-3 w-3 animate-spin" />
                          <span>正在扫描目录...</span>
                        </div>
                      ) : currentFsDirs.length > 0 ? (
                        currentFsDirs.map((dir) => (
                          <div
                            key={dir.path}
                            className="flex items-center justify-between p-1 rounded hover:bg-muted/50 group transition"
                          >
                            <button
                              type="button"
                              onClick={() => navigateFsDir(dir.path)}
                              className="flex items-center space-x-1.5 text-left truncate flex-1 hover:text-emerald-700 dark:hover:text-emerald-400 cursor-pointer"
                            >
                              <Folder className="h-3.5 w-3.5 text-amber-600 dark:text-amber-500 shrink-0" />
                              <span className="truncate">{dir.name}</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => handleSelectFsDir(dir.path)}
                              className="opacity-0 group-hover:opacity-100 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-800 dark:text-emerald-300 hover:bg-emerald-500/20 transition cursor-pointer font-serif-academic"
                            >
                              选用
                            </button>
                          </div>
                        ))
                      ) : (
                        <div className="p-3 text-center text-muted-foreground text-[11px] font-serif-academic">
                          该目录下无子文件夹
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="font-medium text-foreground">项目说明（可选）</label>
              <textarea
                rows={2}
                placeholder="简述项目用途、技术栈、微服务模块划分或关键架构..."
                value={addDesc}
                onChange={(e) => setAddDesc(e.target.value)}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring font-serif-academic"
              />
            </div>

            {addError && (
              <div className="p-2.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs">
                {addError}
              </div>
            )}

            <div className="flex justify-end space-x-2 pt-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setIsAddOpen(false)}
              >
                取消
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={submittingAdd || !addName.trim() || (addPaths.length === 0 && !manualPathInput.trim())}
                className="bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic cursor-pointer"
              >
                {submittingAdd ? "正在接入..." : "确认添加项目"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
};
