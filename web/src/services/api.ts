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
    let payload: any = null;
    try {
      const errJson = await res.json();
      payload = errJson;
      if (errJson.message) errorMsg = errJson.message;
      else if (errJson.detail) errorMsg = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
    } catch {
      // ignore
    }
    // A5：携带状态码与原始错误体，调用方可识别 409 冲突等结构化响应。
    const err = new Error(errorMsg) as Error & { status?: number; payload?: any };
    err.status = res.status;
    err.payload = payload;
    throw err;
  }
  return res.json();
}

async function download(path: string, fallbackName: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    let errorMsg = `HTTP ${res.status} ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson.message) errorMsg = errJson.message;
    } catch {
      // ignore
    }
    throw new Error(errorMsg);
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  const filename = utf8Match ? decodeURIComponent(utf8Match[1]) : plainMatch ? plainMatch[1] : fallbackName;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  return filename;
}

export const api = {
  // Health & Config
  getHealthReady: () => request<HealthReady>("/api/v1/health/ready"),
  getConfig: () => request<any>("/api/v1/config"),
  // A1 全局搜索：跨类型 FTS5 检索与原文定位
  globalSearch: (
    q: string,
    opts?: { types?: string; limit?: number; includeArchived?: boolean },
  ) => {
    const params = new URLSearchParams({ q });
    if (opts?.types) params.set("types", opts.types);
    if (opts?.limit) params.set("limit", String(opts.limit));
    if (opts?.includeArchived) params.set("include_archived", "true");
    return request<{
      query: string;
      total: number;
      items: Array<{
        result_id: string;
        object_type: string;
        object_id: string;
        type_label: string;
        title: string;
        snippet: string;
        match_reason: string;
        updated_at: string;
        locator: Record<string, any>;
        deep_link: string;
        score: number;
      }>;
    }>(`/api/v1/search?${params.toString()}`);
  },
  searchLocate: (resultId: string) =>
    request<{
      result_id: string;
      object_type: string;
      object_id: string;
      type_label: string;
      locator: Record<string, any>;
      deep_link: string;
    }>(`/api/v1/search/${encodeURIComponent(resultId)}/locate`),
  // A5：后端合同为 PUT /api/v1/config/provider（字段与 ProviderConfigUpdateRequest 对齐，
  // extra="forbid"，不得携带多余字段）。
  updateProviderConfig: (data: {
    base_url: string;
    api_key?: string;
    model: string;
    embedding_model?: string;
    reranker_model?: string;
    engine_model?: string;
  }) =>
    request<{ requires_restart: boolean; message: string; provider: any }>("/api/v1/config/provider", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  testProviderConfig: (data: { base_url?: string; api_key?: string; model?: string; embedding_model?: string }) =>
    request<{
      ok: boolean;
      message: string;
      latency_ms?: number | null;
      embedding?: {
        attempted: boolean;
        ok?: boolean | null;
        message: string;
        latency_ms?: number | null;
        dimensions?: number | null;
        input_tokens?: number | null;
      } | null;
    }>("/api/v1/config/provider/test", { method: "POST", body: JSON.stringify(data) }),

  // Overview Stats
  getOverviewStats: () =>
    request<{
      runs: {
        total: number;
        complete: number;
        partial: number;
        working: number;
        failed: number;
        cancelled: number;
        success_rate: number;
      };
      corpus: {
        total_documents: number;
        arxiv_papers: number;
        local_pdf: number;
        local_md: number;
        structured_knowledge: number;
        visual_assets: number;
      };
      corpus_scope: string;
      provider_ok: boolean;
    }>("/api/v1/overview/stats"),

  // Runs / Research
  getRuns: (limit = 50, lifecycle = "active") =>
    request<{ items: RunSummary[] }>(`/api/v1/runs?limit=${limit}&lifecycle=${encodeURIComponent(lifecycle)}`),
  setRunLifecycle: (runId: string, action: "archive" | "delete" | "restore") =>
    request<{ run_id: string; lifecycle: string }>(`/api/v1/runs/${runId}/lifecycle`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
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
  createVerifiedResearchTask: (data: {
    objective: string;
    mode?: "single" | "managed";
    max_subquestions?: number;
  }) =>
    request<{ task_id: string; run_id: string; state: string }>("/api/v1/tasks/verified-research", {
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

  // Export (P6-A1)
  exportRun: (runId: string, format: "markdown" | "bibtex" | "json" | "zip") =>
    download(
      `/api/v1/runs/${encodeURIComponent(runId)}/export?format=${format}`,
      `${runId}.${format === "markdown" ? "md" : format === "bibtex" ? "bib" : format}`
    ),
  exportRunEvidence: (runId: string, evidenceId: string, format: "markdown" | "json") =>
    download(
      `/api/v1/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}/export?format=${format}`,
      `${evidenceId}.${format === "markdown" ? "md" : "json"}`
    ),
  exportChatAnswer: (messageId: string, format: "markdown" | "json") =>
    download(
      `/api/v1/chat/messages/${encodeURIComponent(messageId)}/export?format=${format}`,
      `${messageId}.${format === "markdown" ? "md" : "json"}`
    ),
  exportNote: (noteId: string, format: "markdown" | "json") =>
    download(
      `/api/v1/notes/${encodeURIComponent(noteId)}/export?format=${format}`,
      `${noteId}.${format === "markdown" ? "md" : "json"}`
    ),

  // Run events SSE (P6-A3): cursor-tracked subscription with replay on reconnect.
  // The server replays every event after `after` from persistent run_events, so a
  // reconnect with the last seen cursor neither misses nor duplicates key events.
  subscribeRunEvents: (
    runId: string,
    handlers: {
      onEvent?: (event: { cursor: number; kind: string; state?: string; message?: string; created_at?: string }) => void;
      onTerminal?: (state?: string) => void;
      onOpen?: () => void;
    }
  ): (() => void) => {
    let cursor = 0;
    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const open = () => {
      if (closed) return;
      source = new EventSource(
        `/api/v1/runs/${encodeURIComponent(runId)}/events?after=${cursor}`
      );
      source.onopen = () => handlers.onOpen?.();
      const handleRaw = (raw: MessageEvent) => {
        try {
          const data = JSON.parse(raw.data);
          if (typeof data.cursor === "number" && data.cursor > cursor) cursor = data.cursor;
          handlers.onEvent?.(data);
          if (["complete", "partial", "failed", "cancelled", "expired"].includes(data.state)) {
            handlers.onTerminal?.(data.state);
            closed = true;
            source?.close();
          }
        } catch {
          // ignore malformed frames
        }
      };
      source.onmessage = handleRaw;
      for (const kind of ["progress", "status", "recovery"]) {
        source.addEventListener(kind, handleRaw as EventListener);
      }
      source.onerror = () => {
        source?.close();
        if (!closed) {
          // 断线重连：带最后游标重开，服务端从 run_events 表补发
          reconnectTimer = setTimeout(open, 2000);
        }
      };
    };
    open();
    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
    };
  },

  // Chat & Conversations
  getConversations: (status = "active") =>
    request<{ items: ConversationSummary[] }>(`/api/v1/conversations?status=${encodeURIComponent(status)}`),
  renameConversation: (convId: string, title: string) =>
    request<{ conversation_id: string; title: string; updated_at: string }>(
      `/api/v1/conversations/${encodeURIComponent(convId)}`,
      { method: "PATCH", body: JSON.stringify({ title }) }
    ),
  deleteConversation: (convId: string) =>
    request<{ conversation_id: string; lifecycle: string }>(`/api/v1/conversations/${encodeURIComponent(convId)}`, { method: "DELETE" }),
  restoreConversation: (convId: string) =>
    request<{ conversation_id: string; lifecycle: string }>(`/api/v1/conversations/${encodeURIComponent(convId)}/restore`, { method: "POST" }),
  setConversationLifecycle: (convId: string, action: "archive" | "delete" | "restore") =>
    request<{ conversation_id: string; lifecycle: string }>(
      action === "delete"
        ? `/api/v1/conversations/${encodeURIComponent(convId)}`
        : `/api/v1/conversations/${encodeURIComponent(convId)}/${action}`,
      { method: action === "delete" ? "DELETE" : "POST" }
    ),
  getConversationDetail: (convId: string) =>
    request<{
      conversation_id: string;
      title: string;
      active_mode: string;
      updated_at: string;
      messages: ChatMessage[];
    }>(`/api/v1/conversations/${encodeURIComponent(convId)}`),
  sendChat: (data: {
    question: string;
    conversation_id?: string;
    mode?: string;
    web_search?: boolean;
    thinking_depth?: "quick" | "deep" | "rigorous";
  }) =>
    request<any>("/api/v1/chat", { method: "POST", body: JSON.stringify(data) }),

  // Memory
  getMemories: () => request<{ items: any[] }>("/api/v1/memories"),
  recallMemories: (query: string, projectId?: string) =>
    request<{ query: string; items: any[]; semantic_available: boolean }>(
      `/api/v1/memories/recall?query=${encodeURIComponent(query)}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`
    ),
  deleteMemory: (memoryId: string) => request(`/api/v1/memories/${memoryId}`, { method: "DELETE" }),

  // Library
  getDocuments: (status = "active") =>
    request<{ items: LibraryDocument[]; total: number }>(`/api/v1/library?status=${encodeURIComponent(status)}`),
  setDocumentLifecycle: (documentId: string, action: "archive" | "delete" | "restore") =>
    request<{ document_id: string; lifecycle: string }>(
      `/api/v1/library/documents/${encodeURIComponent(documentId)}/lifecycle`,
      { method: "POST", body: JSON.stringify({ action }) }
    ),
  searchPapers: (params: Record<string, string>) => {
    const qs = new URLSearchParams(params).toString();
    return request<{ items: any[]; total?: number; deduplicated_count?: number; sources?: any[] }>(
      `/api/v1/library/papers/search?${qs}`,
      { method: "POST" }
    ).then((res) => {
      const items: PaperItem[] = (res.items || []).map((p: any) => ({
        id: p.paper_id || p.id,
        title: p.title || "未命名论文",
        authors: p.authors || [],
        doi: p.doi,
        arxiv_id: p.arxiv_id,
        abstract: p.summary || p.abstract || "",
        url: (p.landing_urls && p.landing_urls[0]) || (p.pdf_candidates && p.pdf_candidates[0]?.url) || p.url || "",
        published_year: p.year || (p.published ? parseInt(p.published.slice(0, 4)) : undefined),
        source: Array.isArray(p.sources) ? p.sources.join(", ") : (p.source || "arXiv/OpenAlex"),
        raw_paper: p,
      }));
      return {
        items,
        total: res.deduplicated_count ?? res.total ?? items.length,
        sources: res.sources,
      };
    });
  },
  importPaper: (paper: any, action: "fulltext" | "metadata" = "fulltext") =>
    request<{ status: string; paper_id?: string; document_id?: string; message?: string }>(
      "/api/v1/library/papers",
      {
        method: "POST",
        body: JSON.stringify({ action, paper: paper.raw_paper || paper }),
      }
    ),

  // Document Notes
  getDocumentNote: (noteId: string) => request<DocumentNote>(`/api/v1/notes/${encodeURIComponent(noteId)}`),
  getDocumentNoteRevisions: (noteId: string) => request<{ note_id: string; revisions: any[] }>(`/api/v1/notes/${encodeURIComponent(noteId)}/revisions`),
  analyzeDocument: (docId: string, options?: { path?: string; focus?: string; title?: string }) =>
    request<DocumentNote>("/api/v1/documents/analyze", {
      method: "POST",
      body: JSON.stringify({ document_id: docId, ...options }),
    }),
  // A5 修复：后端 NotePatchRequest 契约字段是 instruction（此前发 patch_prompt
  // 会被 extra="forbid" 契约 422 拒绝，UI 修订从未成功过）。
  patchDocumentNote: (noteId: string, instruction: string, targetVersion = 1, quoteAnchor?: {
    document_id: string;
    quote: string;
    page?: number;
    segment_id?: string;
    asset_id?: string;
    unanchored?: boolean;
  }) =>
    request<DocumentNote>(`/api/v1/notes/${encodeURIComponent(noteId)}/patch`, {
      method: "POST",
      body: JSON.stringify({ instruction, target_version: targetVersion, quote_anchor: quoteAnchor ?? null }),
    }),
  // A2 研读联动：文档分段（含页码定位），供阅读窗格与选择引用锚点使用
  getDocumentSegments: (docId: string) =>
    request<{ document_id: string; segments: Array<{ segment_id: string; ordinal: number; text: string; locator: Record<string, any> }> }>(
      `/api/v1/library/documents/${encodeURIComponent(docId)}`
    ),
  saveNoteToResearch: (noteId: string) =>
    request<{ task_id: string; run_id: string; status: string; report_artifact_id: string; title: string; message: string }>(
      `/api/v1/notes/${encodeURIComponent(noteId)}/save-to-research`,
      { method: "POST" }
    ),

  // Multimodal RAG & Visual Assets
  searchLibraryMultimodal: (query: string, topK = 10, imageK = 5) =>
    request<{
      query: string;
      text_hits_count: number;
      image_hits_count: number;
      fusion_strategy: string;
      fused_hits: Array<{
        hit_id: string;
        score: number;
        rank: number;
        modality: "text" | "image";
        source_snapshot_id: string;
        locator: Record<string, any>;
        text: string;
        asset_id?: string;
        artifact_ref?: string;
        thumbnail_artifact_ref?: string;
        page?: number;
        bbox?: number[];
        parent_chunk_ids?: string[];
      }>;
    }>(`/api/v1/library/search?query=${encodeURIComponent(query)}&top_k=${topK}&image_k=${imageK}`, {
      method: "POST",
    }),
  getLibraryAssets: (params?: { documentId?: string; assetType?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.documentId) sp.append("document_id", params.documentId);
    if (params?.assetType) sp.append("asset_type", params.assetType);
    if (params?.limit) sp.append("limit", params.limit.toString());
    const qs = sp.toString();
    return request<{ total: number; items: any[] }>(`/api/v1/library/assets${qs ? `?${qs}` : ""}`);
  },
  getDocumentAssets: (documentId: string) =>
    request<{ document_id: string; asset_count: number; items: any[] }>(
      `/api/v1/library/documents/${encodeURIComponent(documentId)}/assets`
    ),

  // Projects
  getProjects: () => request<ProjectSummary[]>("/api/v1/projects"),
  createProject: (data: { name: string; root_path?: string; root_paths?: string[]; description?: string }) =>
    request<any>("/api/v1/projects", { method: "POST", body: JSON.stringify(data) }),
  browseFolder: (initialDir?: string) =>
    request<{ path: string | null }>(
      `/api/v1/projects/browse-folder${initialDir ? `?initial_dir=${encodeURIComponent(initialDir)}` : ""}`,
      { method: "POST" }
    ),
  getProjectDetail: (projectId: string) => request<any>(`/api/v1/projects/${projectId}`),
  getProjectTree: (projectId: string) => request<any>(`/api/v1/projects/${projectId}/tree`),
  getProjectFile: (projectId: string, filePath: string) =>
    request<any>(`/api/v1/projects/${projectId}/file?path=${encodeURIComponent(filePath)}`),
  getProjectSemanticDiff: (projectId: string) => request<any>(`/api/v1/projects/${projectId}/git/semantic-diff`),
  proposeProjectCoding: (projectId: string, prompt: string, targetFile?: string) =>
    request<any>(`/api/v1/projects/${projectId}/coding/propose`, {
      method: "POST",
      body: JSON.stringify({
        prompt,
        instruction: prompt,
        target_file: targetFile || "",
      }),
    }),
  applyProjectCoding: (projectId: string, proposalOrId: string | any) => {
    const payload =
      typeof proposalOrId === "string"
        ? { proposal_id: proposalOrId }
        : {
            proposal_id: proposalOrId.proposal_id,
            target_file: proposalOrId.target_file,
            expected_hash: proposalOrId.original_hash,
            proposed_content: proposalOrId.proposed_content,
          };
    return request<any>(`/api/v1/projects/${projectId}/coding/apply`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getProjectWalkthrough: (projectId: string) =>
    request<{
      project_id: string;
      overview: string;
      components: Array<{
        name: string;
        layer: string;
        files: string[];
        responsibilities: string;
        dependencies: string[];
      }>;
      mermaid_topology: string;
      data_flow_description: string;
      theory_mappings: Array<{
        concept: string;
        paper_reference: string;
        code_symbol: string;
        file_path: string;
        line_number: number;
        description: string;
        design_rationale: string;
      }>;
      dependencies_analysis: Record<string, any>;
    }>(`/api/v1/projects/${encodeURIComponent(projectId)}/walkthrough`),
  getProjectAudit: (projectId: string) =>
    request<{
      report_id: string;
      project_id: string;
      summary: string;
      implementation_score: number;
      health_score: number;
      status_counts: Record<string, number>;
      findings: Array<{
        finding_id: string;
        category: string;
        severity: "critical" | "high" | "medium" | "low" | "info";
        title: string;
        description: string;
        target_file: string;
        line_number: number;
        snippet: string;
        recommendation: string;
        implementation_status: string;
      }>;
    }>(`/api/v1/projects/${encodeURIComponent(projectId)}/audit`),
  askProject: (projectId: string, question: string) =>
    request<{
      project_id: string;
      answer_markdown: string;
      cited_files: string[];
      git_evidence: any;
      risks_and_recommendations: string[];
    }>(`/api/v1/projects/${encodeURIComponent(projectId)}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  getProjectLearningGuide: (projectId: string) =>
    request<{
      project_id: string;
      project_name: string;
      project_type: string;
      framework: string;
      primary_language: string;
      total_files: number;
      total_lines: number;
      mental_model: string;
      design_philosophy: string;
      onboarding_roadmap: Array<{
        step_number: number;
        title: string;
        description: string;
        target_files: string[];
        reading_focus: string;
        estimated_minutes: number;
      }>;
      lexicon: Array<{
        name: string;
        kind: string;
        file_path: string;
        line: number;
        summary: string;
      }>;
      vibe_coding_tips: string[];
      architecture_overview: string[];
      entrypoints: string[];
    }>(`/api/v1/projects/${encodeURIComponent(projectId)}/learning-guide`),

  askProjectLearning: (projectId: string, question: string) =>
    request<{
      project_id: string;
      question: string;
      answer: string;
    }>(`/api/v1/projects/${encodeURIComponent(projectId)}/ask-learning`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),

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
