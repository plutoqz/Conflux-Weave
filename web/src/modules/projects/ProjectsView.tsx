import React, { useState, useEffect, useMemo } from "react";
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

export const ProjectsView: React.FC = () => {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [tree, setTree] = useState<any[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>("");
  const [fileContent, setFileContent] = useState<string>("");
  const [diff, setDiff] = useState<string>("");
  const [codingPrompt, setCodingPrompt] = useState<string>("");
  const [proposal, setProposal] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [treeLoading, setTreeLoading] = useState(false);

  // Preview / Code view state
  const [viewMode, setViewMode] = useState<"code" | "preview">("code");
  const [copiedFile, setCopiedFile] = useState(false);

  // Add Project Dialog State
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [addName, setAddName] = useState("");
  const [addPaths, setAddPaths] = useState<string[]>([]);
  const [manualPathInput, setManualPathInput] = useState("");
  const [addDesc, setAddDesc] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [submittingAdd, setSubmittingAdd] = useState(false);
  const [browsingFolder, setBrowsingFolder] = useState(false);

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

  useEffect(() => {
    if (!selectedProjectId) return;
    setTreeLoading(true);
    setSelectedFile("");
    setFileContent("");

    api
      .getProjectTree(selectedProjectId)
      .then((res) => {
        setTree(res.items || res.tree || []);
      })
      .catch(() => setTree([]))
      .finally(() => setTreeLoading(false));

    api
      .getProjectSemanticDiff(selectedProjectId)
      .then((res) => setDiff(res.diff || ""))
      .catch(() => setDiff(""));
  }, [selectedProjectId]);

  const handleSelectFile = async (filePath: string) => {
    setSelectedFile(filePath);
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

  const handleBrowseFolder = async () => {
    setBrowsingFolder(true);
    try {
      const res = await api.browseFolder();
      if (res.path) {
        if (!addPaths.includes(res.path)) {
          const next = [...addPaths, res.path];
          setAddPaths(next);
          if (!addName.trim()) {
            const parts = res.path.split(/[/\\]/).filter(Boolean);
            if (parts.length > 0) setAddName(parts[parts.length - 1]);
          }
        }
      }
    } catch (err: any) {
      alert(`无法打开资源管理器选择目录: ${err.message}`);
    } finally {
      setBrowsingFolder(false);
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

  const handleProposeCoding = async () => {
    if (!codingPrompt.trim() || !selectedProjectId) return;
    setLoading(true);
    try {
      const res = await api.proposeProjectCoding(selectedProjectId, codingPrompt);
      setProposal(res);
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
      await api.applyProjectCoding(selectedProjectId, proposal.proposal_id);
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
            <span className="hidden md:inline-block max-w-sm truncate text-muted-foreground/80" title={selectedProj.root_path || selectedProj.path}>
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
        {/* Left: Project File Tree */}
        <aside className="w-72 shrink-0 border-r border-border/70 bg-card/20 flex flex-col h-full">
          <div className="p-3 border-b border-border/60 flex items-center justify-between">
            <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
              Project Explorer
            </span>
            <span className="text-[10px] font-mono text-muted-foreground">
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

        {/* Middle: Code & Diff Viewer */}
        <main className="flex-1 flex flex-col h-full overflow-hidden bg-background">
          <div className="p-2.5 border-b border-border/60 flex items-center justify-between text-xs text-muted-foreground font-mono bg-muted/20">
            <div className="flex items-center space-x-2 truncate">
              <span className="font-semibold text-foreground truncate max-w-sm">
                {selectedFile || "未选择文件"}
              </span>
              {selectedFile && (
                <Badge variant="outline" className="text-[10px] uppercase font-mono px-1.5 py-0">
                  {getLanguageFromPath(selectedFile)}
                </Badge>
              )}
              {fileContent && (
                <span className="text-[10px] text-muted-foreground hidden sm:inline">
                  {fileContent.split("\n").length} 行 · {(fileContent.length / 1024).toFixed(1)} KB
                </span>
              )}
            </div>
            <div className="flex items-center space-x-2 shrink-0">
              {selectedFile && selectedFile.toLowerCase().endsWith(".md") && (
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
              {diff && (
                <Badge variant="outline" className="text-[10px] shrink-0">
                  包含 Git Diff
                </Badge>
              )}
            </div>
          </div>
          <div
            className="flex-1 overflow-auto code-viewer-container border-t border-border/70"
            id="proj-code-viewer"
          >
            {fileContent ? (
              selectedFile.toLowerCase().endsWith(".md") && viewMode === "preview" ? (
                <div
                  className="p-6 max-w-4xl mx-auto leading-relaxed text-xs sm:text-sm font-serif-academic prose dark:prose-invert"
                  dangerouslySetInnerHTML={{ __html: renderMarkdownWithMath(fileContent) }}
                />
              ) : (
                <div className="flex min-w-full font-mono text-xs leading-5">
                  <div className="select-none py-3 px-3 text-right code-viewer-gutter border-r shrink-0 font-mono text-[11px] leading-5">
                    {fileContent.split("\n").map((_, i) => (
                      <div key={i} className="h-5 leading-5">{i + 1}</div>
                    ))}
                  </div>
                  <pre className="flex-1 py-3 px-4 overflow-x-auto m-0 bg-transparent font-mono text-[12.5px] leading-5 selection:bg-primary/20">
                    <code dangerouslySetInnerHTML={{ __html: highlightCode(fileContent, getLanguageFromPath(selectedFile)) }} />
                  </pre>
                </div>
              )
            ) : (
              <div className="text-center py-24 text-muted-foreground font-serif-academic text-xs sm:text-sm">
                请从左侧文件树点击选择文件进行代码预览、公式渲染与代码治理审查
              </div>
            )}
          </div>
        </main>

        {/* Right: AI Coding Agent Panel */}
        <aside className="w-80 shrink-0 border-l border-border/70 bg-card/30 flex flex-col h-full">
          <div className="p-3 border-b border-border/60 flex items-center space-x-2">
            <Terminal className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
            <h3 className="text-xs sm:text-sm font-semibold text-foreground">AI 代码治理智能体</h3>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-4 text-xs">
            <div className="space-y-2">
              <label className="text-muted-foreground font-medium text-xs">
                重构 / 补丁意图说明
              </label>
              <textarea
                rows={5}
                value={codingPrompt}
                onChange={(e) => setCodingPrompt(e.target.value)}
                placeholder="例如：重构数据验证模块，添加异常处理与边界单元测试..."
                className="w-full text-xs p-2.5 rounded-md border border-input bg-transparent focus:outline-none focus:ring-1 focus:ring-ring font-mono"
              />
              <Button
                id="proj-coding-propose-btn"
                size="sm"
                className="w-full gap-1.5 mt-1 bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic"
                disabled={loading || !codingPrompt.trim()}
                onClick={handleProposeCoding}
              >
                <Play className="h-3.5 w-3.5" />
                <span>{loading ? "分析并生成补丁中..." : "生成代码治理提案"}</span>
              </Button>
            </div>

            {proposal && (
              <div className="p-3.5 rounded-lg border border-emerald-500/40 bg-emerald-500/5 space-y-2.5">
                <div className="flex items-center justify-between font-medium text-emerald-700 dark:text-emerald-400">
                  <span className="font-mono text-xs">提案 ID: {proposal.proposal_id}</span>
                </div>
                <p className="text-foreground/80 text-xs leading-relaxed font-serif-academic">
                  {proposal.summary || "已生成语义补丁方案"}
                </p>
                <Button
                  id="proj-prop-apply-btn"
                  size="sm"
                  variant="default"
                  className="w-full gap-1 bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic"
                  onClick={handleApplyCoding}
                >
                  <Check className="h-3.5 w-3.5" />
                  <span>应用此补丁至工作区</span>
                </Button>
              </div>
            )}
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
                        className="text-muted-foreground hover:text-rose-500 p-0.5 rounded transition shrink-0 ml-2"
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
                  className="h-8 px-3 text-xs font-serif-academic shrink-0"
                >
                  添加路径
                </Button>
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
                className="bg-emerald-800 hover:bg-emerald-900 text-white font-serif-academic"
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
