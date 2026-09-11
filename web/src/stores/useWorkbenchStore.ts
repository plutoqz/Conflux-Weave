import { create } from "zustand";
import type { SectionType, RunSummary, RunDetail, HealthReady } from "@/types/workbench";

interface WorkbenchState {
  section: SectionType;
  setSection: (section: SectionType) => void;

  theme: "light" | "dark";
  toggleTheme: () => void;

  zenMode: boolean;
  toggleZenMode: () => void;
  setZenMode: (val: boolean) => void;

  hudOpen: boolean;
  toggleHud: () => void;

  runs: RunSummary[];
  setRuns: (runs: RunSummary[]) => void;
  activeRunId: string | null;
  setActiveRunId: (id: string | null) => void;
  activeRunDetail: RunDetail | null;
  setActiveRunDetail: (detail: RunDetail | null) => void;

  health: HealthReady | null;
  setHealth: (health: HealthReady | null) => void;

  // Modals
  isNewTaskOpen: boolean;
  setIsNewTaskOpen: (open: boolean) => void;
  isFollowUpOpen: boolean;
  setIsFollowUpOpen: (open: boolean) => void;
}

const getInitialTheme = (): "light" | "dark" => {
  try {
    const search = window.location.search || window.location.hash.split("?")[1] || "";
    const params = new URLSearchParams(search);
    const themeParam = params.get("theme");
    if (themeParam === "light" || themeParam === "dark") {
      return themeParam;
    }
  } catch {}
  return (
    (localStorage.getItem("cw_theme") as "light" | "dark") ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
  );
};

const initialTheme = getInitialTheme();

if (initialTheme === "dark") {
  document.documentElement.classList.add("dark");
} else {
  document.documentElement.classList.remove("dark");
}

export const useWorkbenchStore = create<WorkbenchState>((set) => ({
  section: "overview",
  setSection: (section) => {
    window.location.hash = `#/${section}`;
    set({ section });
  },

  theme: initialTheme,
  toggleTheme: () =>
    set((state) => {
      const next = state.theme === "dark" ? "light" : "dark";
      localStorage.setItem("cw_theme", next);
      if (next === "dark") {
        document.documentElement.classList.add("dark");
      } else {
        document.documentElement.classList.remove("dark");
      }
      return { theme: next };
    }),

  zenMode: false,
  toggleZenMode: () => set((s) => ({ zenMode: !s.zenMode })),
  setZenMode: (val) => set({ zenMode: val }),

  hudOpen: false,
  toggleHud: () => set((s) => ({ hudOpen: !s.hudOpen })),

  runs: [],
  setRuns: (runs) => set({ runs }),
  activeRunId: null,
  setActiveRunId: (id) => set({ activeRunId: id }),
  activeRunDetail: null,
  setActiveRunDetail: (detail) => set({ activeRunDetail: detail }),

  health: null,
  setHealth: (health) => set({ health }),

  isNewTaskOpen: false,
  setIsNewTaskOpen: (open) => set({ isNewTaskOpen: open }),
  isFollowUpOpen: false,
  setIsFollowUpOpen: (open) => set({ isFollowUpOpen: open }),
}));
