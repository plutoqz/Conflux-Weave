import React, { useState, useEffect } from "react";
import {
  FolderGit2,
  GitBranch,
  FileCode,
  Folder,
  ChevronRight,
  ChevronDown,
  Terminal,
  Play,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { ProjectSummary } from "@/types/workbench";

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

  useEffect(() => {
    api.getProjects()
      .then((res) => {
        setProjects(res || []);
        if (res && res.length > 0) {
          setSelectedProjectId(res[0].project_id);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedProjectId) return;
    api.getProjectTree(selectedProjectId)
      .then((res) => setTree(res.tree || []))
      .catch(() => setTree([]));

    api.getProjectSemanticDiff(selectedProjectId)
      .then((res) => setDiff(res.diff || ""))
      .catch(() => setDiff(""));
  }, [selectedProjectId]);

  const handleSelectFile = async (filePath: string) => {
    setSelectedFile(filePath);
    try {
      const res = await api.getProjectFile(selectedProjectId, filePath);
      setFileContent(res.content || "");
    } catch {
      setFileContent("// 无法读取该文件内容或为空");
    }
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
    } catch (e: any) {
      alert(`应用失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const selectedProj = projects.find((p) => p.project_id === selectedProjectId);

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* Top Project Bar */}
      <div className="shrink-0 flex items-center justify-between px-6 py-2.5 border-b border-border/70 bg-card/40">
        <div className="flex items-center space-x-3">
          <FolderGit2 className="h-4 w-4 text-emerald-500" />
          <select
            id="project-select"
            value={selectedProjectId}
            onChange={(e) => setSelectedProjectId(e.target.value)}
            className="text-xs font-semibold bg-muted/60 border border-input rounded-md px-2.5 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
          >
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name} ({p.path})
              </option>
            ))}
          </select>
        </div>

        {/* Git Info Badge */}
        {selectedProj && (
          <div className="flex items-center space-x-3 text-xs font-mono text-muted-foreground">
            <div className="flex items-center space-x-1" id="proj-git-branch">
              <GitBranch className="h-3.5 w-3.5 text-emerald-500" />
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
        <aside className="w-64 shrink-0 border-r border-border/70 bg-card/20 flex flex-col h-full">
          <div className="p-3 border-b border-border/60">
            <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
              Repository Files
            </span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-0.5 text-xs font-mono" id="project-file-tree">
            {tree.length === 0 ? (
              <div className="p-4 text-muted-foreground text-center">正在加载目录结构...</div>
            ) : (
              tree.map((item, idx) => (
                <button
                  key={idx}
                  onClick={() => handleSelectFile(item.path || item)}
                  className={cn(
                    "w-full text-left px-2 py-1 rounded flex items-center space-x-2 transition truncate",
                    selectedFile === (item.path || item)
                      ? "bg-accent text-accent-foreground font-semibold"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
                  )}
                >
                  <FileCode className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                  <span className="truncate">{item.path || item}</span>
                </button>
              ))
            )}
          </div>
        </aside>

        {/* Middle: Code & Diff Viewer */}
        <main className="flex-1 flex flex-col h-full overflow-hidden bg-background">
          <div className="p-2 border-b border-border/60 flex items-center justify-between text-xs text-muted-foreground font-mono bg-muted/20">
            <span>{selectedFile || "未选择文件"}</span>
            {diff && <Badge variant="outline" className="text-[10px]">包含变更 Diff</Badge>}
          </div>
          <div className="flex-1 overflow-auto p-4 font-mono text-xs leading-relaxed" id="proj-code-viewer">
            {fileContent ? (
              <pre className="whitespace-pre">{fileContent}</pre>
            ) : (
              <div className="text-center py-20 text-muted-foreground">
                请从左侧文件树选择文件进行审查
              </div>
            )}
          </div>
        </main>

        {/* Right: AI Coding Agent Panel */}
        <aside className="w-80 shrink-0 border-l border-border/70 bg-card/30 flex flex-col h-full">
          <div className="p-3 border-b border-border/60 flex items-center space-x-2">
            <Terminal className="h-4 w-4 text-emerald-500" />
            <h3 className="text-xs font-semibold text-foreground">AI 代码治理智能体</h3>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-4 text-xs">
            <div className="space-y-1.5">
              <label className="text-muted-foreground font-medium">重构 / 补丁意图说明</label>
              <textarea
                rows={4}
                value={codingPrompt}
                onChange={(e) => setCodingPrompt(e.target.value)}
                placeholder="例如：重构数据验证模块，添加异常处理与边界单元测试..."
                className="w-full text-xs p-2 rounded-md border border-input bg-transparent focus:outline-none focus:ring-1 focus:ring-ring"
              />
              <Button
                id="proj-coding-propose-btn"
                size="sm"
                className="w-full gap-1.5 mt-1"
                disabled={loading || !codingPrompt.trim()}
                onClick={handleProposeCoding}
              >
                <Play className="h-3.5 w-3.5" />
                <span>{loading ? "分析并生成补丁中..." : "生成代码治理提案"}</span>
              </Button>
            </div>

            {proposal && (
              <div className="p-3 rounded-lg border border-emerald-500/40 bg-emerald-500/5 space-y-2">
                <div className="flex items-center justify-between font-medium text-emerald-600 dark:text-emerald-400">
                  <span>提案已就绪: {proposal.proposal_id}</span>
                </div>
                <p className="text-muted-foreground text-[11px] leading-relaxed">
                  {proposal.summary || "已生成语义补丁"}
                </p>
                <Button
                  id="proj-prop-apply-btn"
                  size="sm"
                  variant="default"
                  className="w-full gap-1"
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
    </div>
  );
};
