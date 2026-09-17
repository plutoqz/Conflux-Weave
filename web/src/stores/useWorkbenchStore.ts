import { create } from "zustand";
import type { SectionType, RunSummary, RunDetail, HealthReady, TopicRecord } from "@/types/workbench";
import { api } from "@/services/api";

export type FontSizePreference = "normal" | "medium" | "large" | "xlarge";

export interface GlobalBackgroundTask {
  id: string;
  title: string;
  type: "reading" | "research" | "project";
  status: "running" | "succeeded" | "failed";
  startTime: number;
  message?: string;
  targetId?: string;
}

interface WorkbenchState {
  section: SectionType;
  setSection: (section: SectionType) => void;

  theme: "light" | "dark";
  toggleTheme: () => void;

  fontSize: FontSizePreference;
  setFontSize: (size: FontSizePreference) => void;

  zenMode: boolean;
  toggleZenMode: () => void;
  setZenMode: (val: boolean) => void;

  hudOpen: boolean;
  toggleHud: () => void;

  runs: RunSummary[];
  setRuns: (runs: RunSummary[]) => void;
  refreshRuns: () => Promise<void>;
  activeRunId: string | null;
  setActiveRunId: (id: string | null) => void;
  activeRunDetail: RunDetail | null;
  setActiveRunDetail: (detail: RunDetail | null) => void;

  health: HealthReady | null;
  setHealth: (health: HealthReady | null) => void;

  // Background Task Ledger
  backgroundTasks: GlobalBackgroundTask[];
  addBackgroundTask: (task: GlobalBackgroundTask) => void;
  updateBackgroundTask: (id: string, updates: Partial<GlobalBackgroundTask>) => void;
  removeBackgroundTask: (id: string) => void;

  // Global Note Studio Opener
  activeNoteDocId: string | null;
  isNoteStudioOpen: boolean;
  openNoteStudio: (docId: string) => void;
  closeNoteStudio: () => void;

  // Modals
  isNewTaskOpen: boolean;
  setIsNewTaskOpen: (open: boolean) => void;
  isFollowUpOpen: boolean;
  setIsFollowUpOpen: (open: boolean) => void;

  // Research Topics (Gate 5 U21)
  topics: TopicRecord[];
  setTopics: (topics: TopicRecord[]) => void;
  activeTopicId: string | null;
  setActiveTopicId: (id: string | null) => void;
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

const getInitialFontSize = (): FontSizePreference => {
  try {
    const saved = localStorage.getItem("cw_font_size") as FontSizePreference;
    if (saved && ["normal", "medium", "large", "xlarge"].includes(saved)) {
      return saved;
    }
  } catch {}
  return "normal";
};

const initialTheme = getInitialTheme();
if (initialTheme === "dark") {
  document.documentElement.classList.add("dark");
} else {
  document.documentElement.classList.remove("dark");
}

const initialFontSize = getInitialFontSize();
document.documentElement.setAttribute("data-font-size", initialFontSize);

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

  fontSize: initialFontSize,
  setFontSize: (size) => {
    localStorage.setItem("cw_font_size", size);
    document.documentElement.setAttribute("data-font-size", size);
    set({ fontSize: size });
  },

  zenMode: false,
  toggleZenMode: () => set((s) => ({ zenMode: !s.zenMode })),
  setZenMode: (val) => set({ zenMode: val }),

  hudOpen: false,
  toggleHud: () => set((s) => ({ hudOpen: !s.hudOpen })),

  runs: [],
  setRuns: (runs) => set({ runs }),
  refreshRuns: async () => {
    try {
      const res = await api.getRuns();
      set({ runs: res.items || [] });
    } catch {}
  },
  activeRunId: null,
  setActiveRunId: (id) => {
    try {
      if (id) localStorage.setItem("cw_active_run_id", id);
      else localStorage.removeItem("cw_active_run_id");
    } catch {}
    set({ activeRunId: id });
  },
  activeRunDetail: null,
  setActiveRunDetail: (detail) => set({ activeRunDetail: detail }),

  health: null,
  setHealth: (health) => set({ health }),

  backgroundTasks: [],
  addBackgroundTask: (task) =>
    set((s) => ({
      backgroundTasks: [task, ...s.backgroundTasks.filter((t) => t.id !== task.id)],
    })),
  updateBackgroundTask: (id, updates) =>
    set((s) => ({
      backgroundTasks: s.backgroundTasks.map((t) => (t.id === id ? { ...t, ...updates } : t)),
    })),
  removeBackgroundTask: (id) =>
    set((s) => ({
      backgroundTasks: s.backgroundTasks.filter((t) => t.id !== id),
    })),

  activeNoteDocId: null,
  isNoteStudioOpen: false,
  openNoteStudio: (docId) => set({ activeNoteDocId: docId, isNoteStudioOpen: true }),
  closeNoteStudio: () => set({ isNoteStudioOpen: false }),

  isNewTaskOpen: false,
  setIsNewTaskOpen: (open) => set({ isNewTaskOpen: open }),
  isFollowUpOpen: false,
  setIsFollowUpOpen: (open) => set({ isFollowUpOpen: open }),

  topics: [],
  setTopics: (topics) => set({ topics }),
  activeTopicId: null,
  setActiveTopicId: (id) => set({ activeTopicId: id }),
}));
