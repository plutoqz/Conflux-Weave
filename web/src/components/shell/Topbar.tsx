import React from "react";
import { Sun, Moon, Sparkles, Activity } from "lucide-react";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { Button } from "@/components/ui/button";
import type { SectionType } from "@/types/workbench";

const navItems: Array<{ id: SectionType; label: string }> = [
  { id: "overview", label: "总览" },
  { id: "chat", label: "对话" },
  { id: "research", label: "研究" },
  { id: "library", label: "资料库" },
  { id: "projects", label: "项目" },
  { id: "settings", label: "设置" },
];

export const Topbar: React.FC = () => {
  const { section, setSection, theme, toggleTheme, health } = useWorkbenchStore();

  const isReady = health?.status === "ready";

  return (
    <header className="sticky top-0 z-40 flex h-14 w-full items-center justify-between border-b border-border/70 bg-background/80 px-4 backdrop-blur-md">
      {/* Brand & Nav */}
      <div className="flex items-center space-x-6">
        <button
          onClick={() => setSection("overview")}
          className="flex items-center space-x-2.5 text-left transition hover:opacity-90"
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-900 text-white font-serif-academic font-bold text-xs shadow-xs top-bevel">
            CW
          </div>
          <div className="hidden sm:flex flex-col">
            <span className="text-sm font-serif-academic font-semibold tracking-tight leading-none text-foreground">
              Conflux Weave
            </span>
            <span className="text-xs text-foreground/75 font-mono leading-none mt-1 font-medium">
              Research Workbench
            </span>
          </div>
        </button>

        <nav className="flex items-center space-x-1" aria-label="工作台分区">
          {navItems.map((item) => {
            const active = section === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setSection(item.id)}
                className={`relative px-3.5 py-1.5 text-xs sm:text-sm font-serif-academic transition-all rounded-md ${
                  active
                    ? "bg-card text-foreground font-semibold shadow-2xs border border-border/70"
                    : "text-foreground/75 hover:text-foreground hover:bg-muted/50"
                }`}
              >
                {item.label}
                {active && (
                  <span className="absolute bottom-0 left-2 right-2 h-[2px] bg-emerald-800 dark:bg-emerald-400 rounded-full" />
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Right Actions */}
      <div className="flex items-center space-x-3">
        {/* Health Dot */}
        <div
          className="flex items-center space-x-2 rounded-full border border-border/80 bg-muted/40 px-3 py-1 text-xs text-foreground/80 font-medium"
          title={isReady ? "系统就绪" : "系统正在检查或降级"}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              isReady ? "bg-emerald-500 animate-pulse" : "bg-amber-500"
            }`}
          />
          <span className="font-mono text-xs font-semibold">
            {isReady ? "READY" : "CHECKING"}
          </span>
        </div>

        {/* Theme Toggle */}
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggleTheme}
          title={theme === "dark" ? "切换至浅色模式" : "切换至深色模式"}
          aria-label="切换深浅主题"
        >
          {theme === "dark" ? (
            <Sun className="h-4 w-4 text-amber-400" />
          ) : (
            <Moon className="h-4 w-4 text-muted-foreground" />
          )}
        </Button>
      </div>
    </header>
  );
};
