export type SectionType =
  | "overview"
  | "chat"
  | "research"
  | "library"
  | "projects"
  | "topics"
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
  task_family?: string;
  kind?: string;
  title?: string;
  state?: RunStatus;
  status?: RunStatus;
  status_message?: string;
  is_terminal?: boolean;
  user_state?: string;
  workflow_version?: string;
  query?: string;
  created_at: string;
  updated_at: string;
  evidence_count?: number;
  duration_seconds?: number;
  lifecycle?: "active" | "archived" | "deleted";
  is_favorite?: boolean;
  tags?: string[];
  last_accessed_at?: string;
  project_id?: string;
  note_ids?: string[];
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

export interface RunBudget {
  state?: string;
  input_tokens_used?: number;
  output_tokens_used?: number;
  tool_calls_used?: number;
  retrieval_rounds_used?: number;
  input_tokens_limit?: number;
  output_tokens_limit?: number;
  tool_calls_limit?: number;
  retrieval_rounds_limit?: number;
  tool_calls_reserved?: number;
  wall_clock_seconds_reserved?: number;
  tool_calls_remaining?: number;
  wall_clock_seconds_remaining?: number;
  estimated_cost_limit?: string;
  cost_enforcement?: string;
}

export interface RunProgress {
  completed_steps?: number;
  total_steps?: number;
  active?: boolean;
  current_phase?: string;
  current_step_kind?: string;
  last_event_message?: string;
  last_event_at?: string | null;
  updated_at?: string | null;
}

export interface RunDetail {
  run_id: string;
  task_id: string;
  state?: string;
  status?: string;
  created_at: string;
  updated_at?: string;
  progress?: RunProgress;
  budget?: RunBudget;
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
  message_id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
  mode?: "auto" | "direct" | "rag" | "deep" | "document" | "project";
  run_id?: string;
  document_ids?: string[];
  memory_candidates?: Array<{
    candidate_id: string;
    statement?: string;
    key?: string;
    value?: string;
    scope?: string;
    category?: string;
    reason?: string;
    status?: "pending" | "approved" | "rejected";
  }>;
  image_assets?: Array<{
    asset_id: string;
    caption: string;
    page?: number | null;
    url: string;
  }>;
  citations?: Array<{
    index: number;
    chunk_id: string;
    source_snapshot_id: string;
    locator?: string | Record<string, any>;
  }>;
  verification?: "model-knowledge" | "unverified-aggregation" | "durable-run-dispatched";
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  active_mode: string;
  lifecycle?: "active" | "archived" | "deleted";
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
  segment_count?: number;
  character_count?: number;
  size_bytes?: number;
  asset_count?: number;
  status?: string;
  media_type?: string;
  lifecycle?: "active" | "archived" | "deleted";
  doi?: string;
  arxiv_id?: string;
  error?: string;
}

export interface LibraryOverview {
  total: number;
  imported: number;
  processing?: number;
  failed?: number;
  character_count: number;
  size_bytes: number;
  source_counts?: Record<string, number>;
  index_status: string;
  manifest?: string;
  items: LibraryDocument[];
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
  raw_paper?: any;
  status?: string;
  pdf_path?: string;
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
  metadata?: Record<string, any> & {
    reading_coverage?: {
      mode: string;
      mode_label: string;
      total_segments: number;
      covered_segments_count: number;
      uncovered_segments_count: number;
      coverage_percentage: number;
      covered_headings: string[];
      uncovered_headings: string[];
      truncated: boolean;
    };
  };
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
  run_id?: string;
  skill_id: string;
  status: "completed" | "success" | "failed" | "needs_input";
  summary?: string;
  content: string;
  tokens_consumed: number;
  elapsed_seconds: number;
  structured_data?: Record<string, any>;
  artifacts?: Array<{ artifact_id: string; name: string; media_type?: string }>;
  tool_traces?: Array<{ tool: string; status: string; summary: string; [key: string]: any }>;
  error?: string | null;
  error_message?: string | null;
}

export interface ProjectMessage {
  message_id: string;
  project_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  target_tab?: string | null;
  cited_files?: string[];
  selected_snippet?: string | null;
}

export interface CodingVerifyResult {
  proposal_id: string;
  command: string;
  success: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_seconds: number;
}

export interface CodingRevertResult {
  proposal_id: string;
  success: boolean;
  message: string;
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

export interface SkillVersion {
  skill_id: string;
  version: string;
  name: string;
  description: string;
  category: string;
  author: string;
  change_summary?: string;
  created_at: string;
}

export interface SchemaCompatibilityReport {
  compatible: boolean;
  breaking_changes: string[];
  warnings: string[];
}

export interface MCPProbeResult {
  server_id: string;
  status: "healthy" | "unhealthy";
  latency_ms: number;
  tool_count: number;
  last_probe_at: string;
  error?: string | null;
}

export interface MCPDriftResult {
  server_id: string;
  has_drift: boolean;
  added_tools: string[];
  removed_tools: string[];
  modified_tools: string[];
  checked_at: string;
}

export interface ComputeSandboxStatus {
  platform: string;
  os_sandbox_mechanism: string;
  os_isolation_enforced: boolean;
  network_blocked: boolean;
  max_timeout_seconds: number;
  max_memory_bytes: number;
  max_output_bytes: number;
}

export interface DAGTaskNode {
  task_id: string;
  agent_role: string;
  instruction: string;
  dependencies: string[];
  required_tools: string[];
  timeout_seconds: number;
  status: "pending" | "ready" | "waiting_approval" | "running" | "completed" | "failed" | "skipped" | "cancelled" | "paused";
  result_summary?: string | null;
  tokens_consumed: number;
  error_message?: string | null;
  condition?: Record<string, any> | string | null;
  requires_approval?: boolean;
  approval_status?: string | null;
  join_policy?: "all" | "any" | "first_success" | string;
}

export interface DAGExecutionPlan {
  plan_id: string;
  objective: string;
  tasks: DAGTaskNode[];
  execution_order: string[][];
  total_tokens_consumed: number;
  created_at: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "paused" | "waiting_approval";
  is_paused?: boolean;
  waiting_approval_nodes?: string[];
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

export interface TopicRecord {
  topic_id: string;
  name: string;
  objective?: string;
  description?: string;
  tags?: string[];
  document_ids?: string[];
  note_ids?: string[];
  run_ids?: string[];
  project_ids?: string[];
  conversation_ids?: string[];
  recent_location?: {
    section?: string;
    object_id?: string;
    label?: string;
    timestamp?: string;
  };
  created_at: string;
  updated_at: string;
}

export interface TopicDetail extends TopicRecord {
  documents?: Array<{
    document_id: string;
    title: string;
    lifecycle?: string;
    process_state?: string;
    available: boolean;
  }>;
  notes?: Array<{
    note_id: string;
    document_id?: string;
    title: string;
    version?: number;
    available: boolean;
  }>;
  runs?: Array<{
    run_id: string;
    title: string;
    state: string;
    created_at: string;
    available: boolean;
  }>;
  projects?: Array<{
    project_id: string;
    name: string;
    root_path?: string;
    description?: string;
    available: boolean;
  }>;
  conversations?: Array<{
    conversation_id: string;
    title: string;
    message_count?: number;
    available: boolean;
  }>;
}

export interface TopicListResponse {
  items: TopicRecord[];
  total: number;
}

export interface TopicLinkPayload {
  object_type: "document" | "note" | "run" | "project" | "conversation";
  object_id: string;
  action: "link" | "unlink";
}

// P7-A4 研究任务组织 (Research Task Organization)
export interface RunOrganizationRecord {
  run_id: string;
  is_favorite: boolean;
  tags: string[];
  last_accessed_at?: string;
  project_id?: string;
  note_ids: string[];
  updated_at: string;
}

export interface ProjectRunGroupItem {
  project_id?: string;
  project_name: string;
  run_count: number;
  runs: RunSummary[];
}

export interface ProjectRunGroupingResponse {
  groups: ProjectRunGroupItem[];
  total_runs: number;
}

export interface ResearchTaskTemplate {
  template_id: string;
  name: string;
  description: string;
  task_family: string;
  category: string;
  default_query: string;
  tags: string[];
  suggested_budgets: Record<string, any>;
  variables_schema: Array<{
    name: string;
    label: string;
    placeholder?: string;
    required?: boolean;
  }>;
  is_builtin: boolean;
  created_at: string;
}

export interface ContinuationProposal {
  predecessor_run_id: string;
  parent_query: string;
  recommended_task_family: string;
  suggested_query: string;
  inherited_project_id?: string;
  inherited_tags: string[];
  inherited_note_ids: string[];
  unresolved_limitations: string[];
  unmet_criteria: string[];
  context_summary: string;
}

export interface ContinueResearchResponse {
  task_id: string;
  run_id: string;
  predecessor_run_id: string;
  inherited_project_id?: string;
  tags: string[];
  state: string;
  created: boolean;
}

// --- P7-C2 Provider Resilience & Structured Output Types ---
export interface ProviderHealthItem {
  endpoint_id: string;
  name: string;
  model: string;
  priority: number;
  base_url: string;
  circuit_state: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
  can_attempt: boolean;
  failure_count: number;
  last_error: string;
  last_failure_time: number;
  supports_json_object: boolean;
  supports_thinking_toggle: boolean;
}

export interface ProviderHealthResponse {
  endpoints: ProviderHealthItem[];
  timestamp: number;
}

export interface ResetCircuitResponse {
  endpoint_id: string;
  reset: boolean;
  circuit_state: string;
}

export interface StructuredRepairRequest {
  raw_text: string;
  schema?: Record<string, any>;
  fill_defaults?: boolean;
}

export interface StructuredRepairResponse {
  success: boolean;
  parsed_data?: any;
  warnings: string[];
  error?: string | null;
}




// --- Deep Research V2 Long-Horizon Survey Types ---
export interface LongHorizonSurveySubmitRequest {
  objective: string;
  background_context?: string;
  max_sources?: number;
  allow_network?: boolean;
}

export interface ResearchProbeItem {
  probe_id: string;
  target_aspect: string;
  query_intent: string;
  expected_data_type: string;
}

export interface SurveyChapterPlanItem {
  chapter_id: string;
  chapter_index: number;
  title: string;
  core_thesis: string;
  probes: ResearchProbeItem[];
  depends_on: string[];
}

export interface LongHorizonSurveyOutlineResponse {
  outline_id: string;
  objective: string;
  background_summary: string;
  target_word_count_range: [number, number];
  chapters: SurveyChapterPlanItem[];
  total_probes_count: number;
}

export interface SurveyReportSectionItem {
  chapter_id: string;
  chapter_index: number;
  title: string;
  content_markdown: string;
  word_count: number;
  cited_source_urls: string[];
}

export interface LongHorizonSurveyReportResponse {
  report_id: string;
  run_id?: string;
  objective: string;
  executive_summary: string[];
  sections: SurveyReportSectionItem[];
  evolution_timeline_mermaid: string;
  comparison_matrix_markdown: string;
  total_word_count: number;
  total_sources_cited: number;
  citations_ledger: Array<{
    source_id?: string;
    title: string;
    url: string;
    authority_score?: number;
    source_channel?: string;
  }>;
  full_markdown: string;
}

// --- Paper Reading V2 Types ---
export interface PaperClassifyRequest {
  title: string;
  abstract?: string;
  section_headings?: string[];
  full_text_sample?: string;
}

export interface PaperClassifyResponse {
  paradigm: string;
  confidence: number;
  rationale: string;
  chapters: Array<{
    key: string;
    title: string;
    target_aspect: string;
    expected_visual_types: string[];
  }>;
}

export interface PaperCitationGraphRequest {
  paper_id?: string;
  title: string;
  raw_text?: string;
  doi?: string | null;
  allow_network?: boolean;
}

export interface PaperCitationGraphResponse {
  seed_paper_title: string;
  seed_paper_id: string;
  backward_foundations: Array<{
    paper_id: string;
    title: string;
    authors: string[];
    year?: number | null;
    venue?: string | null;
    citation_type: string;
    summary_or_context: string;
  }>;
  forward_derivatives: Array<{
    paper_id: string;
    title: string;
    authors: string[];
    year?: number | null;
    venue?: string | null;
    citation_type: string;
    summary_or_context: string;
  }>;
  local_references: Array<{
    paper_id: string;
    title: string;
    authors: string[];
    year?: number | null;
    summary_or_context: string;
  }>;
  mermaid_graph: string;
}

export interface CrossPaperCompareRequest {
  papers: Array<{
    paper_id?: string;
    title: string;
    raw_text?: string;
    year?: number | null;
  }>;
}

export interface CrossPaperCompareResponse {
  entities: Array<{
    paper_id: string;
    title: string;
    year?: number | null;
    paradigm: string;
    core_problem: string;
    key_architecture_mechanism: string;
    compute_space_overhead: string;
    main_benchmark_metrics: string;
    tradeoffs_and_risks: string;
  }>;
  dimension_matrix_markdown: string;
  lineage_mermaid_timeline: string;
  synthesis_narrative: string;
}

export interface PaperReadingV2SubmitRequest {
  paper_id?: string;
  title: string;
  raw_text?: string;
  pdf_path?: string | null;
  doi?: string | null;
  section_headings?: string[];
  allow_network?: boolean;
}

export interface PaperReadingV2ReportResponse {
  paper_id: string;
  title: string;
  paradigm: string;
  outline: Record<string, any>;
  markdown_content: string;
  visual_slices: Array<{
    asset_id: string;
    page: number;
    asset_type: string;
    caption: string;
    image_rel_path: string;
    deep_analysis: string;
  }>;
  citation_graph?: PaperCitationGraphResponse | null;
  metadata: Record<string, any>;
  created_at: string;
}

// --- Project Tutor V2 Types ---
export interface ProjectTutorDissectRequest {
  project_path: string;
  project_name?: string;
}

export interface ProjectTutorDissectResponse {
  project_id: string;
  project_name: string;
  root_path: string;
  mental_model_summary: string;
  decisions: Array<{
    topic: string;
    chosen_solution: string;
    rejected_alternatives: string[];
    core_rationale: string;
    tradeoffs_accepted: string;
  }>;
  topology_mermaid: string;
  dataflow_sequence_mermaid: string;
  dependency_cards: Array<{
    package_name: string;
    category: string;
    role_in_project: string;
    underlying_mechanism: string;
    why_needed: string;
  }>;
  walkthrough_stages: Array<{
    stage_index: number;
    stage_name: string;
    core_theme: string;
    key_files: string[];
    reading_tips: string;
  }>;
  hygiene_score: number;
  executive_verdict: string;
  findings_count: number;
  full_markdown: string;
}

export interface ProjectHygieneAuditRequest {
  project_path: string;
  max_files?: number;
}

export interface ProjectHygieneAuditResponse {
  project_path: string;
  hygiene_score: number;
  total_findings_count: number;
  findings_by_category: Record<string, number>;
  findings: Array<{
    finding_id: string;
    category: string;
    severity: string;
    file_path: string;
    line_number: number;
    code_snippet: string;
    issue_description: string;
    harm_analysis: string;
    refactor_diff_suggestion: string;
  }>;
  executive_verdict: string;
}


