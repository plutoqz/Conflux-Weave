import React, { useEffect, useState } from "react";
import { Topbar } from "@/components/shell/Topbar";
import { Sidebar } from "@/components/shell/Sidebar";
import { TaskDialog } from "@/components/shell/TaskDialog";
import { FollowUpDialog } from "@/components/shell/FollowUpDialog";
import { NoteStudioDialog } from "@/modules/notes/NoteStudioDialog";
import { OverviewView } from "@/modules/overview/OverviewView";
import { ChatView } from "@/modules/chat/ChatView";
import { ResearchView } from "@/modules/research/ResearchView";
import { LibraryView } from "@/modules/library/LibraryView";
import { ProjectsView } from "@/modules/projects/ProjectsView";
import { SettingsView } from "@/modules/settings/SettingsView";
import { useWorkbenchStore } from "@/stores/useWorkbenchStore";
import { api } from "@/services/api";
import type { SectionType } from "@/types/workbench";

export const App: React.FC = () => {
  const { section, setSection, setRuns, setHealth, setActiveRunId } = useWorkbenchStore();
  const [selectedNoteDocId, setSelectedNoteDocId] = useState<string | null>(null);
  const [isNoteOpen, setIsNoteOpen] = useState(false);

  // Sync route with window.location.hash
  useEffect(() => {
    const handleHash = () => {
      const rawHash = window.location.hash.replace(/^#\/?/, "");
      const [sectionPart, queryPart] = rawHash.split("?");
      if (
        ["overview", "chat", "research", "library", "projects", "settings"].includes(sectionPart)
      ) {
        setSection(sectionPart as SectionType);
      }
      if (queryPart) {
        const params = new URLSearchParams(queryPart);
        const runId = params.get("run") || params.get("run_id");
        if (runId) {
          setActiveRunId(runId);
        }
      }
    };
    handleHash();
    window.addEventListener("hashchange", handleHash);
    return () => window.removeEventListener("hashchange", handleHash);
  }, [setSection, setActiveRunId]);

  // Initial fetch of runs and health status
  useEffect(() => {
    api.getRuns().then((res) => setRuns(res.items || [])).catch(() => {});
    api.getHealthReady().then(setHealth).catch(() => {});
  }, [setRuns, setHealth]);

  const handleOpenNote = (docId: string) => {
    setSelectedNoteDocId(docId);
    setIsNoteOpen(true);
  };

  return (
    <div className="flex flex-col min-h-screen bg-background text-foreground selection:bg-emerald-500/20 selection:text-emerald-400">
      <Topbar />

      <div className="flex-1 flex overflow-hidden">
        {/* Runs Sidebar is visible only on Research section */}
        {section === "research" && <Sidebar />}

        {/* Main Content Workspace */}
        <main className="flex-1 overflow-y-auto">
          {section === "overview" && <OverviewView />}
          {section === "chat" && <ChatView />}
          {section === "research" && <ResearchView />}
          {section === "library" && <LibraryView onOpenNote={handleOpenNote} />}
          {section === "projects" && <ProjectsView />}
          {section === "settings" && <SettingsView />}
        </main>
      </div>

      {/* Global Dialogs */}
      <TaskDialog />
      <FollowUpDialog />
      <NoteStudioDialog
        documentId={selectedNoteDocId}
        open={isNoteOpen}
        onOpenChange={setIsNoteOpen}
      />
    </div>
  );
};
