import React, { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { api } from "@/services/api";

interface SearchHit {
  result_id: string;
  object_type: string;
  object_id: string;
  type_label: string;
  title: string;
  snippet: string;
  match_reason: string;
  updated_at: string;
  deep_link: string;
}

const TYPE_BADGE: Record<string, string> = {
  chat_message: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  run: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  note: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  document: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  paper: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  evidence: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
};

export const GlobalSearch: React.FC = () => {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    const onDocClick = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const runSearch = (value: string) => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    if (!value.trim()) {
      setHits([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    debounceRef.current = window.setTimeout(async () => {
      try {
        const res = await api.globalSearch(value, { limit: 12 });
        setHits(res.items || []);
      } catch {
        setHits([]);
      } finally {
        setLoading(false);
      }
    }, 250);
  };

  const jump = (hit: SearchHit) => {
    setOpen(false);
    setQuery("");
    setHits([]);
    // 只写 hash：App.tsx 的 handleHash 是路由单一来源（hashchange →
    // setSection + setActiveRunId），先 setSection 会用裸分区覆写查询参数。
    window.location.hash = hit.deep_link.startsWith("#") ? hit.deep_link.slice(1) : hit.deep_link;
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "Enter" && hits.length > 0) {
      jump(hits[0]);
    }
  };

  return (
    <div ref={boxRef} className="relative hidden md:block w-64 lg:w-80">
      <div className="flex items-center gap-2 rounded-md border border-border/80 bg-muted/40 px-3 py-1.5">
        <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        <input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            runSearch(event.target.value);
          }}
          onFocus={() => query && setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="全局搜索：对话 / 报告 / 笔记 / 文献 / 证据"
          aria-label="全局搜索"
          className="w-full bg-transparent text-xs outline-none placeholder:text-muted-foreground/70"
        />
        {loading && <span className="h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground/40 border-t-transparent" />}
      </div>
      {open && query.trim() && (
        <div className="absolute right-0 top-full z-50 mt-1 max-h-[420px] w-[min(560px,90vw)] overflow-y-auto rounded-md border border-border bg-card shadow-lg">
          {hits.length === 0 && !loading && (
            <div className="px-4 py-3 text-xs text-muted-foreground">无匹配结果（检索不触发模型调用）</div>
          )}
          {hits.map((hit) => (
            <button
              key={hit.result_id}
              onClick={() => jump(hit)}
              className="block w-full border-b border-border/50 px-3 py-2 text-left transition hover:bg-accent/60 last:border-b-0"
            >
              <div className="flex items-center gap-2">
                <span
                  className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${
                    TYPE_BADGE[hit.object_type] || "bg-muted text-foreground"
                  }`}
                >
                  {hit.type_label}
                </span>
                <span className="truncate text-xs font-medium text-foreground">{hit.title || hit.object_id}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">{hit.match_reason}</span>
              </div>
              {hit.snippet && (
                <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">{hit.snippet}</p>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

