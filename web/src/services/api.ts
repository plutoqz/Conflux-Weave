import type {
  HealthReady,
  RunSummary,
  RunDetail,
  ChatMessage,
  ConversationSummary,
  LibraryDocument,
  PaperItem,
  ProjectSummary,
  DocumentNote,
  SkillSummary,
  SkillDetail,
  SkillExecuteResult,
  MCPServer,
  DAGExecutionPlan,
  AgentEvent,
} from "@/types/workbench";

const API_BASE = "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    let errorMsg = `HTTP ${res.status} ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson.message) errorMsg = errJson.message;
      else if (errJson.detail) errorMsg = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
    } catch {
      // ignore
    }
    throw new Error(errorMsg);
  }
  return res.json();
}

export const api = {
  // Health & Config
  getHealthReady: () => request<HealthReady>("/api/v1/health/ready"),
  getConfig: () => request<any>("/api/v1/config"),
  updateProviderConfig: (data: { provider: string; base_url?: string; api_key?: string; model?: string }) =>
    request<any>("/api/v1/config/provider", { method: "POST", body: JSON.stringify(data) }),

  // Runs / Research
  getRuns: (limit = 50) => request<{ items: RunSummary[] }>(`/api/v1/runs?limit=${limit}`),
  getRunDetail: (runId: string) => request<RunDetail>(`/api/v1/runs/${runId}`),
  getArtifactContent: (runId: string, artifactId: string) =>
    request<{ artifact: any; content: string }>(`/api/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/content`),
  getEvidence: (evidenceId: string, runId?: string) =>
    request<any>(
      `/api/v1/evidence/${encodeURIComponent(evidenceId)}${
        runId ? `?run_id=${encodeURIComponent(runId)}` : ""
      }`
    ),
  createResearchTask: (data: { query: string; topics?: string[]; max_results?: number }) =>
    request<{ task_id: string; run_id: string }>("/api/v1/tasks/research", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  createFixtureTask: (data: { objective: string; idempotency_key?: string }) =>
    request<{ task_id: string; run_id: string }>("/api/v1/tasks/research-fixture", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  retryRun: (runId: string) => request(`/api/v1/runs/${runId}/retry_unknown_external`, { method: "POST" }),
  failRun: (runId: string) => request(`/api/v1/runs/${runId}/fail_unknown_external`, { method: "POST" }),
  rerunRun: (runId: string) => request(`/api/v1/runs/${runId}/rerun`, { method: "POST" }),
  followUpRun: (runId: string, query: string) =>
    request(`/api/v1/runs/${runId}/follow-up`, { method: "POST", body: JSON.stringify({ query }) }),

  // Chat & Conversations
  getConversations: () => request<{ items: ConversationSummary[] }>("/api/v1/conversations"),
  getConversationDetail: (convId: string) =>
    request<{
      conversation_id: string;
      title: string;
      active_mode: string;
      updated_at: string;
      messages: ChatMessage[];
    }>(`/api/v1/conversations/${encodeURIComponent(convId)}`),
  getConversationMessages: (convId: string) => request<{ items: ChatMessage[] }>(`/api/v1/chat/messages?conversation_id=${convId}`),
  sendChat: (data: { question: string; conversation_id?: string; mode?: string }) =>
    request<any>("/api/v1/chat", { method: "POST", body: JSON.stringify(data) }),

  // Memory
  getMemories: () => request<{ items: any[] }>("/api/v1/memories"),
  deleteMemory: (memoryId: string) => request(`/api/v1/memories/${memoryId}`, { method: "DELETE" }),

  // Library
  getDocuments: () => request<{ items: LibraryDocument[] }>("/api/v1/library"),
  searchPapers: (params: Record<string, string>) => {
    const qs = new URLSearchParams(params).toString();
    return request<{ items: PaperItem[]; total?: number; sources?: any[] }>(`/api/v1/library/search?${qs}`);
  },

  // Document Notes
  getDocumentNote: (noteId: string) => request<DocumentNote>(`/api/v1/notes/${encodeURIComponent(noteId)}`),
  getDocumentNoteRevisions: (noteId: string) => request<{ note_id: string; revisions: any[] }>(`/api/v1/notes/${encodeURIComponent(noteId)}/revisions`),
  analyzeDocument: (docId: string, options?: { path?: string; focus?: string; title?: string }) =>
    request<DocumentNote>("/api/v1/documents/analyze", {
      method: "POST",
      body: JSON.stringify({ document_id: docId, ...options }),
    }),
  patchDocumentNote: (noteId: string, patchPrompt: string, targetVersion = 1) =>
    request<DocumentNote>(`/api/v1/notes/${encodeURIComponent(noteId)}/patch`, {
      method: "POST",
      body: JSON.stringify({ patch_prompt: patchPrompt, target_version: targetVersion }),
    }),

  // Projects
  getProjects: () => request<ProjectSummary[]>("/api/v1/projects"),
  getProjectDetail: (projectId: string) => request<any>(`/api/v1/projects/${projectId}`),
  getProjectTree: (projectId: string) => request<any>(`/api/v1/projects/${projectId}/tree`),
  getProjectFile: (projectId: string, filePath: string) =>
    request<any>(`/api/v1/projects/${projectId}/file?path=${encodeURIComponent(filePath)}`),
  getProjectSemanticDiff: (projectId: string) => request<any>(`/api/v1/projects/${projectId}/git/semantic-diff`),
  proposeProjectCoding: (projectId: string, prompt: string) =>
    request<any>(`/api/v1/projects/${projectId}/coding/propose`, { method: "POST", body: JSON.stringify({ prompt }) }),
  applyProjectCoding: (projectId: string, proposalId: string) =>
    request<any>(`/api/v1/projects/${projectId}/coding/apply`, { method: "POST", body: JSON.stringify({ proposal_id: proposalId }) }),

  // Skills Studio (P5.1)
  getSkills: () => request<{ items: SkillSummary[]; count: number }>("/api/v1/skills"),
  getSkill: (skillId: string) => request<SkillDetail>(`/api/v1/skills/${encodeURIComponent(skillId)}`),
  executeSkill: (skillId: string, inputs: Record<string, any>) =>
    request<SkillExecuteResult>(`/api/v1/skills/${encodeURIComponent(skillId)}/execute`, {
      method: "POST",
      body: JSON.stringify({ inputs }),
    }),

  // MCP Gateway (P5.2 / P5.3)
  getMcpServers: () => request<{ items: MCPServer[]; count: number }>("/api/v1/mcp/servers"),
  registerMcpServer: (data: Partial<MCPServer>) =>
    request<MCPServer>("/api/v1/mcp/servers", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deleteMcpServer: (serverId: string) =>
    request<any>(`/api/v1/mcp/servers/${encodeURIComponent(serverId)}`, { method: "DELETE" }),
  syncMcpServer: (serverId: string) =>
    request<MCPServer>(`/api/v1/mcp/servers/${encodeURIComponent(serverId)}/sync`, { method: "POST" }),

  // DAG & Multi-Agent Orchestration (P5.4)
  validateDagPlan: (data: { objective: string; tasks: any[] }) =>
    request<{ valid: boolean; execution_order: string[][]; errors: string[] }>("/api/v1/dag/plans/validate", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  executeDagPlan: (data: { objective: string; tasks: any[]; run_id?: string }) =>
    request<DAGExecutionPlan>("/api/v1/dag/plans/execute", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getAgentEvents: (runId: string) =>
    request<{ items: AgentEvent[]; count: number }>(`/api/v1/runs/${encodeURIComponent(runId)}/agent-events`),
};
