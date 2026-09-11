import React, { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { api } from "@/services/api";

export const FollowUpDialog: React.FC = () => {
  const {
    isFollowUpOpen,
    setIsFollowUpOpen,
    activeRunId,
    setRuns,
    setActiveRunId,
  } = useWorkbenchStore();

  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || !activeRunId) return;

    setSubmitting(true);
    setError(null);
    try {
      const result = await api.followUpRun(activeRunId, query.trim());
      setIsFollowUpOpen(false);
      setQuery("");

      const updated = await api.getRuns();
      setRuns(updated.items || []);
      if (result && (result as any).run_id) {
        setActiveRunId((result as any).run_id);
      }
    } catch (err: any) {
      setError(err.message || "跟进提问失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={isFollowUpOpen} onOpenChange={setIsFollowUpOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>跟进深度追问 (Follow-up)</DialogTitle>
          <DialogDescription>
            基于当前研究成果的上下文与证据谱系，提出进一步的假设或深挖特定子问题。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">追问内容</label>
            <textarea
              required
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="例如：在上述第 3 节中提到的方案，实验上的保真度衰减机制具体是什么？"
              className="w-full text-xs p-2.5 rounded-md border border-input bg-transparent focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>

          {error && <div className="text-xs text-rose-500 bg-rose-500/10 p-2 rounded-md">{error}</div>}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setIsFollowUpOpen(false)}
            >
              取消
            </Button>
            <Button type="submit" size="sm" disabled={submitting}>
              {submitting ? "提交中..." : "启动追问"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};
