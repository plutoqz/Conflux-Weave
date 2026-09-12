import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { FileText, Copy, Sparkles, Check, History, Loader2, AlertCircle } from "lucide-react";
import { marked } from "marked";
import { renderMarkdownWithMath, cleanDocumentNoteText } from "@/lib/math";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import type { DocumentNote } from "@/types/workbench";

interface NoteStudioDialogProps {
  documentId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const NoteStudioDialog: React.FC<NoteStudioDialogProps> = ({
  documentId,
  open,
  onOpenChange,
}) => {
  const [note, setNote] = useState<DocumentNote | null>(null);
  const [activeTab, setActiveTab] = useState<"html" | "md">("html");
  const [patchPrompt, setPatchPrompt] = useState("");
  const [patching, setPatching] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revisions, setRevisions] = useState<any[]>([]);
  const [savingToResearch, setSavingToResearch] = useState(false);
  const [savedResearchRunId, setSavedResearchRunId] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !documentId) {
      setNote(null);
      setError(null);
      setRevisions([]);
      setSavedResearchRunId(null);
      return;
    }
    setLoading(true);
    setError(null);
    setSavedResearchRunId(null);

    // Call analyzeDocument (which gets existing or builds new)
    api.analyzeDocument(documentId)
      .then(async (res) => {
        setNote(res);
        if (res.note_id) {
          try {
            const revRes = await api.getDocumentNoteRevisions(res.note_id);
            setRevisions(revRes.revisions || []);
          } catch {
            // ignore
          }
        }
      })
      .catch((err) => {
        console.error("Failed to analyze document:", err);
        setError(err.message || "文档研读失败，请检查文件是否存在全文。");
      })
      .finally(() => setLoading(false));
  }, [open, documentId]);

  const handleSelectRevision = async (noteId: string) => {
    if (!noteId) return;
    try {
      setLoading(true);
      const revNote = await api.getDocumentNote(noteId);
      setNote(revNote);
    } catch (err: any) {
      alert(`无法加载指定版本: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleApplyPatch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!patchPrompt.trim() || !note) return;

    setPatching(true);
    try {
      const updated = await api.patchDocumentNote(note.note_id, patchPrompt.trim(), note.version);
      setNote(updated);
      setPatchPrompt("");
      // Refresh revisions
      const revRes = await api.getDocumentNoteRevisions(updated.note_id);
      setRevisions(revRes.revisions || []);
    } catch (err: any) {
      alert(`修订失败: ${err.message}`);
    } finally {
      setPatching(false);
    }
  };

  const handleSaveToResearch = async () => {
    if (!note?.note_id) return;
    try {
      setSavingToResearch(true);
      const res = await api.saveNoteToResearch(note.note_id);
      setSavedResearchRunId(res.run_id);
    } catch (err: any) {
      alert(`保存报告到研究失败: ${err.message || err}`);
    } finally {
      setSavingToResearch(false);
    }
  };

  const rawHtml = note?.html_content || note?.content_html || "";
  const rawMd = note?.markdown_content || note?.content_markdown || "";

  const mdContent = cleanDocumentNoteText(rawMd);

  // Prepared HTML for iframe: if htmlContent is present, enhance it; otherwise parse mdContent
  let displayHtml = "";
  if (rawHtml) {
    let enhancedHtml = cleanDocumentNoteText(rawHtml);
    if (!enhancedHtml.includes("katex.min.css")) {
      const katexHead = `
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body, {delimiters: [{left: '$$', right: '$$', display: true}, {left: '\\\\[', right: '\\\\]', display: true}, {left: '$', right: '$', display: false}, {left: '\\\\(', right: '\\\\)', display: false}], throwOnError: false});"></script>
      `;
      if (enhancedHtml.includes("</head>")) {
        enhancedHtml = enhancedHtml.replace("</head>", `${katexHead}</head>`);
      } else {
        enhancedHtml = `${katexHead}${enhancedHtml}`;
      }
    }
    displayHtml = enhancedHtml;
  } else if (mdContent) {
    const renderedBody = renderMarkdownWithMath(mdContent);
    displayHtml = `<!DOCTYPE html><html><head><meta charset="utf-8">
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
      <style>
        body { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; padding: 24px 32px; line-height: 1.85; color: #181816; background: #FAF9F6; margin: 0; }
        h1 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #1B4931; margin-top: 1.6em; border-bottom: 2px solid #1B4931; padding-bottom: 8px; font-size: 1.6rem; font-weight: 700; }
        h2 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #6d28d9; border-left: 4px solid #8b5cf6; padding-left: 10px; margin-top: 1.5em; font-size: 1.3rem; font-weight: 700; }
        h3 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #0369a1; border-left: 3.5px solid #0ea5e9; padding-left: 9px; margin-top: 1.3em; font-size: 1.12rem; font-weight: 650; }
        h4 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #0f766e; border-left: 3px solid #06b6d4; padding-left: 8px; margin-top: 1.2em; font-size: 1.02rem; font-weight: 600; }
        h5 { font-family: "江西拙楷", "JiangXiZhuoKai", "LXGW WenKai", "Charter", "Source Serif 4", Georgia, serif; color: #15803d; border-left: 3px solid #10b981; padding-left: 8px; margin-top: 1.1em; font-size: 0.96rem; font-weight: 600; }
        h6 { font-family: "江西拙楷", "JiangXiZhuoKai", "Charter", "Source Serif 4", Georgia, serif; color: #be123c; border-left: 3px solid #f43f5e; padding-left: 8px; margin-top: 1.0em; font-size: 0.92rem; font-weight: 600; }
        table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }
        th, td { border: 1px solid #D5D3CC; padding: 8px 12px; text-align: left; }
        th { background: #EAE8E0; font-weight: 600; }
        code { background: #EAE8E0; padding: 2px 6px; border-radius: 4px; font-size: 12px; font-family: monospace; }
        blockquote { border-left: 3px solid #1B4931; margin: 16px 0; padding-left: 16px; color: #555; }
        .katex-display { margin: 16px 0; overflow-x: auto; text-align: center; }
      </style></head><body>${renderedBody}</body></html>`;
  } else {
    displayHtml = "<p style='padding: 24px; color: #888;'>暂无内容</p>";
  }

  const handleCopyMd = () => {
    if (mdContent) {
      navigator.clipboard.writeText(mdContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl h-[85vh] flex flex-col p-0 gap-0 overflow-hidden bg-background">
        {/* Note Studio Header */}
        <div className="p-4 border-b border-border/70 flex items-center justify-between bg-card/60">
          <div className="flex items-center space-x-3 min-w-0">
            <div className="h-8 w-8 rounded-lg bg-emerald-900/10 text-emerald-800 dark:text-emerald-300 flex items-center justify-center shrink-0">
              <FileText className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center space-x-2">
                <h3 className="text-base font-serif-academic font-bold text-foreground truncate max-w-md">
                  {note?.title || "文档研读笔记"}
                </h3>
                {note && (
                  <Badge variant="outline" className="text-xs font-mono border-emerald-800/40 text-emerald-800 dark:text-emerald-300 bg-emerald-800/5 font-semibold">
                    v{note.version}
                  </Badge>
                )}
              </div>
              <p className="text-xs text-foreground/75 font-mono truncate max-w-sm mt-0.5">
                Doc: {documentId}
              </p>
            </div>
          </div>

          {/* Revisions & View Mode Tabs */}
          <div className="flex items-center space-x-2 shrink-0">
            {revisions.length > 1 && (
              <div className="flex items-center space-x-1 text-xs">
                <History className="h-3.5 w-3.5 text-muted-foreground" />
                <select
                  value={note?.note_id || ""}
                  onChange={(e) => handleSelectRevision(e.target.value)}
                  className="bg-muted text-xs rounded px-2 py-1 border border-border/80 focus:outline-none font-serif-academic"
                >
                  {revisions.map((rev) => (
                    <option key={rev.note_id} value={rev.note_id}>
                      v{rev.version} {rev.instruction ? `(${rev.instruction.slice(0, 10)}...)` : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="flex bg-muted/80 p-0.5 rounded-lg text-xs font-serif-academic">
              <button
                onClick={() => setActiveTab("html")}
                className={cn(
                  "px-2.5 py-1 rounded-md transition font-medium",
                  activeTab === "html"
                    ? "bg-background text-foreground shadow-xs font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                HTML 视图
              </button>
              <button
                onClick={() => setActiveTab("md")}
                className={cn(
                  "px-2.5 py-1 rounded-md transition font-medium",
                  activeTab === "md"
                    ? "bg-background text-foreground shadow-xs font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                Markdown 原文
              </button>
            </div>

            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopyMd}
              disabled={!mdContent}
              title="复制 Markdown 原文"
              className="h-7 px-2 text-xs font-serif-academic gap-1 text-muted-foreground hover:text-foreground"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
              <span>{copied ? "已复制" : "复制"}</span>
            </Button>

            {savedResearchRunId ? (
              <Badge variant="outline" className="text-xs font-serif-academic border-emerald-600 text-emerald-700 bg-emerald-50 dark:bg-emerald-950/40 dark:text-emerald-300 gap-1 py-1 px-2.5">
                <Check className="h-3 w-3 text-emerald-600" />
                <span>已保存到研究 ({savedResearchRunId.slice(0, 12)})</span>
              </Badge>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={handleSaveToResearch}
                disabled={!note || savingToResearch}
                title="将当前研读报告归档并保存到深度研究中心"
                className="h-7 px-2.5 text-xs font-serif-academic gap-1.5 border-emerald-700/50 text-emerald-800 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-950/50"
              >
                {savingToResearch ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-400" />
                )}
                <span>保存报告到研究</span>
              </Button>
            )}
          </div>
        </div>

        {/* Note Body */}
        <div className="flex-1 overflow-hidden bg-background relative">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-3 p-6">
              <Loader2 className="h-7 w-7 animate-spin text-emerald-800 dark:text-emerald-400" />
              <div className="space-y-1">
                <p className="text-sm font-serif-academic font-medium text-foreground">
                  DocumentAgent 正在研读文档...
                </p>
                <p className="text-xs sm:text-sm font-serif-academic text-foreground/75 max-w-sm">
                  正在解析长文档切片、提炼核心概念与推导脉络，请稍候。
                </p>
              </div>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-3 p-6">
              <AlertCircle className="h-8 w-8 text-rose-500" />
              <div className="space-y-1">
                <p className="text-sm font-serif-academic font-semibold text-foreground">
                  文档研读未能完成
                </p>
                <p className="text-xs sm:text-sm font-serif-academic text-foreground/80 max-w-md">
                  {error}
                </p>
              </div>
            </div>
          ) : !note ? (
            <div className="flex items-center justify-center h-full text-xs sm:text-sm text-foreground/75 font-serif-academic">
              未找到此文档的研读笔记。
            </div>
          ) : activeTab === "html" ? (
            <iframe
              title="Note HTML Preview"
              srcDoc={displayHtml}
              sandbox="allow-same-origin allow-scripts"
              className="w-full h-full border-0 bg-transparent"
            />
          ) : (
            <div className="p-6 overflow-auto h-full font-mono text-xs sm:text-sm text-foreground whitespace-pre-wrap leading-relaxed bg-muted/10 selection:bg-emerald-800/20">
              {mdContent || "暂无 Markdown 原文"}
            </div>
          )}
        </div>

        {/* Patch Studio Footer */}
        <div className="p-3 border-t border-border/70 bg-card/60 flex items-center space-x-2">
          <Sparkles className="h-4 w-4 text-emerald-800 dark:text-emerald-400 shrink-0" />
          <form onSubmit={handleApplyPatch} className="flex-1 flex gap-2">
            <Input
              value={patchPrompt}
              onChange={(e) => setPatchPrompt(e.target.value)}
              placeholder="输入自然语言修订意图（例如：“精炼第三节推导过程”、“增加概念对比表”、“补充核心算法局限性”）..."
              className="text-xs sm:text-sm flex-1 font-serif-academic"
              disabled={patching || !note}
            />
            <Button
              type="submit"
              size="sm"
              disabled={patching || !patchPrompt.trim() || !note}
              className="shrink-0 text-xs font-serif-academic bg-emerald-800 hover:bg-emerald-900 text-white"
            >
              {patching ? "正在演进修订..." : "应用修订"}
            </Button>
          </form>
        </div>
      </DialogContent>
    </Dialog>
  );
};
