export type SectionType =
  | "overview"
  | "chat"
  | "research"
  | "library"
  | "projects"
  | "skills"
  | "settings";

export type RunStatus =
  | "accepted"
  | "queued"
  | "working"
  | "complete"
  | "partial"
  | "failed"
  | "cancelled"
  | "expired"
  | "needs_attention";

export interface RunSummary {
  run_id: string;
  task_id: string;
  kind?: string;
  title?: string;
  status: RunStatus;
  user_state?: string;
  workflow_version?: string;
  query?: string;
  created_at: string;
  updated_at: string;
  evidence_count?: number;
  duration_seconds?: number;
}

export interface DeliveryRecord {
  disposition: "complete" | "partial" | "failed" | "no_answer" | "inconclusive";
  artifact_ids?: string[];
  evidence_ids?: string[];
  artifact_refs?: string[];
  evidence_refs?: string[];
  limitations?: string[];
  unmet_criteria?: string[];
  recovery_actions?: string[];
}

export interface EvidenceRecord {
  evidence_id: string;
  quote: string;
  source_snapshot_id?: string;
  locator?: Record<string, any>;
  extraction_method?: string;
}

export interface StepRecord {
  step_id: string;
  kind: string;
  attempt: number;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  started_at?: string;
  completed_at?: string;
}

export interface RunDetail {
  run_id: string;
  task_id: string;
  state?: string;
  status?: string;
  created_at: string;
  updated_at?: string;
  delivery?: DeliveryRecord | null;
  report_content?: string | null;
  evidence?: EvidenceRecord[];
  steps?: StepRecord[];
  query?: string;
  task_family?: string;
  research_context?: {
    query?: string;
    objective?: string;
  };
  task_input?: {
    query?: string;
    objective?: string;
    topics?: string[];
  };
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
  mode?: "auto" | "direct" | "rag" | "deep" | "document" | "project";
  run_id?: string;
  memory_candidates?: Array<{
    candidate_id: string;
    key: string;
    value: string;
    reason: string;
  }>;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  active_mode: string;
}

export interface HealthCheckItem {
  name: string;
  status: string;
  message?: string | null;
  recovery_action?: string | null;
}

export interface HealthReady {
  status: "ready" | "degraded" | "not_ready";
  checks?: HealthCheckItem[];
  corpus_scope?: string;
  provider_ready?: boolean;
}

export interface LibraryDocument {
  document_id: string;
  title: string;
  source_type: string;
  created_at: string;
  relative_path?: string;
  chunk_count?: number;
  asset_count?: number;
}

export interface PaperItem {
  id: string;
  title: string;
  authors: string[];
  doi?: string;
  arxiv_id?: string;
  abstract?: string;
  url?: string;
  published_year?: number;
  source: string;
  match_snippet?: string;
}

export interface ProjectSummary {
  project_id: string;
  name: string;
  root_path?: string;
  root_paths?: string[];
  path?: string;
  description?: string;
  branch?: string;
  dirty_files?: number;
}

export interface DocumentNote {
  note_id: string;
  document_id: string;
  title: string;
  version: number;
  parent_note_id?: string | null;
  applied_patch_id?: string | null;
  executive_summary?: string;
  markdown_content?: string;
  html_content?: string;
  content_markdown?: string;
  content_html?: string;
  created_at: string;
  revisions?: Array<{ note_id?: string; revision_id?: string; version: number; instruction?: string; created_at: string }>;
}

export interface SkillBudget {
  max_tokens: number;
  timeout_seconds: number;
  max_steps?: number;
  estimated_time_seconds?: number;
}

export interface SkillSummary {
  skill_id: string;
  name: string;
  category: "academic" | "engineering" | "writing" | "custom";
  description: string;
  author: string;
  version: string;
  required_tools: string[];
  default_budget: SkillBudget;
  created_at: string;
}

export interface SkillDetail extends SkillSummary {
  prompt_template?: string;
  rules?: string[];
  input_schema: {
    type: string;
    properties?: Record<string, any>;
    required?: string[];
  };
  output_schema: {
    type: string;
    properties?: Record<string, any>;
    required?: string[];
  };
}

export interface SkillExecuteResult {
  run_id: string;
  skill_id: string;
  status: "success" | "failed";
  content: string;
  tokens_consumed: number;
  elapsed_seconds: number;
  error_message?: string | null;
}

export interface MCPTool {
  name: string;
  description: string;
  parameters: Record<string, any>;
}

export interface MCPServer {
  server_id: string;
  name: string;
  transport: "stdio" | "sse";
  command?: string | null;
  args?: string[] | null;
  url?: string | null;
  headers?: Record<string, string> | null;
  status: "connected" | "disconnected" | "error";
  tools: MCPTool[];
  created_at: string;
  updated_at: string;
}

export interface DAGTaskNode {
  task_id: string;
  agent_role: string;
  instruction: string;
  dependencies: string[];
  required_tools: string[];
  timeout_seconds: number;
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "cancelled";
  result_summary?: string | null;
  tokens_consumed: number;
  error_message?: string | null;
}

export interface DAGExecutionPlan {
  plan_id: string;
  objective: string;
  tasks: DAGTaskNode[];
  execution_order: string[][];
  total_tokens_consumed: number;
  created_at: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
}

export interface AgentEvent {
  event_id: string;
  run_id: string;
  event_type: string;
  sender: string;
  receiver: string;
  payload: Record<string, any>;
  timestamp: string;
}

