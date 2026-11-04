export interface PageInfo {
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
}

export interface ResourceVersionSummary {
  version_id: string;
  resource_id: string;
  version: string;
  name: string;
  status: string;
  artifact_hash: string;
  manifest_snapshot_hash: string;
  status_changed_at: string;
  created_at: string;
  row_version: number;
}

export interface ResourceSummary {
  resource_id: string;
  kind: string;
  slug: string;
  owner_team: string;
  created_by: string;
  selected_version: ResourceVersionSummary | null;
  created_at: string;
  updated_at: string;
  row_version: number;
}

export interface ResourceVersion extends ResourceVersionSummary {
  schema_version: string;
  description: string;
  owner_team: string;
  entrypoint: string;
  manifest: Record<string, unknown>;
  artifacts: ArtifactReference[];
  runtimes: RuntimeValidation[];
  dependencies: ResolvedDependency[];
  status_changed_by: string;
  status_reason: string;
  validated_at: string | null;
  approved_at: string | null;
  published_at: string | null;
  deprecated_at: string | null;
  retire_after: string | null;
  retired_at: string | null;
  replacement_version_id: string | null;
  created_by: string;
  updated_at: string;
}

export interface ResourceDetail extends ResourceSummary {
  version: ResourceVersion | null;
  versions: ResourceVersionSummary[];
}

export interface ArtifactReference {
  path: string;
  digest: string;
  size_bytes: number;
  media_type?: string | null;
}

export interface RuntimeValidation {
  runtime: string;
  validation_status: string;
  adapter_version?: string | null;
}

export interface ResolvedDependency {
  kind: string;
  slug: string;
  version: string;
  optional: boolean;
  source: string;
  purpose?: string | null;
  ordinal: number;
  resolution_status: string;
  resolved_resource_version_id?: string | null;
}

export interface ResourceReview {
  review_id: string;
  resource_version_id: string;
  review_type: string;
  reviewer_id: string;
  reviewer_role: string;
  decision: string;
  artifact_hash: string;
  manifest_snapshot_hash: string;
  runtime_scope: string[];
  rationale: string;
  decided_at: string;
  invalidated_at: string | null;
}

export interface Job {
  job_id: string;
  job_kind: string;
  queue_name: string;
  status: string;
  attempt: number;
  max_attempts: number;
  correlation_id: string;
  payload_hash: string;
  resource_version_id: string | null;
  error_code: string | null;
  error_summary: string | null;
  created_at: string;
  row_version: number;
}

export interface JobEvent {
  job_id: string;
  sequence_no: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  actor_type: string;
  actor_id: string;
  payload: Record<string, unknown>;
  occurred_at: string;
}

export interface AuditRecord {
  audit_id: string;
  occurred_at: string;
  actor_id: string;
  actor_type: string;
  action: string;
  outcome: string;
  entity_type: string;
  entity_public_id: string;
  resource_version_id: string | null;
  from_status: string | null;
  to_status: string | null;
  reason_code: string | null;
  reason_summary: string | null;
  event_hash: string;
  /** The hash of the preceding entry; a break here means the chain is not intact. */
  previous_event_hash: string | null;
  artifact_hash: string | null;
  correlation_id: string;
}

export interface ListResponse<T> {
  items: T[];
  page: PageInfo;
}

export interface ErrorBody {
  code: string;
  detail?: string;
  trace_id?: string | null;
  status: number;
}

// --- Stage 2 search -------------------------------------------------------

export type RankingProfile = "hybrid" | "keyword" | "semantic";
export type SearchSort = "relevance" | "newest" | "name";

export interface SearchFilters {
  kinds?: string[];
  statuses?: string[];
  runtimes?: string[];
  owner_teams?: string[];
  tags?: string[];
  categories?: string[];
  required_permissions?: string[];
}

export interface SearchRequest {
  query: string;
  filters: SearchFilters;
  limit: number;
  ranking_profile?: RankingProfile;
  sort?: SearchSort;
  include_highlights?: boolean;
}

export interface SearchHit {
  rank: number;
  score: number;
  score_components?: Record<string, number> | null;
  resource: ResourceSummary;
  version: ResourceVersionSummary;
  source_artifact_hash: string;
  matched_fields: string[];
  highlights: string[];
  index_revision?: string | null;
}

export interface SearchResponse {
  query_id: string;
  items: SearchHit[];
  page: PageInfo;
  took_ms: number;
}

/** Headers carrying facts the frozen response body cannot hold. */
export interface SearchDiagnostics {
  degraded: string[];
  indexRevision: string;
}

/**
 * Capability names the server reports for the signed-in caller.
 *
 * These are the *permission to attempt* an operation, not a guarantee it will
 * succeed: business rules can still reject a request. The server remains the
 * only authority; this list exists so the console does not render dead buttons.
 */
export type Capability =
  | "resource:read"
  | "resource:create"
  | "resource:edit"
  | "resource:delete"
  | "search:use"
  | "audit:read"
  | "lifecycle:submit"
  | "lifecycle:validate"
  | "lifecycle:publish"
  | "lifecycle:reverse_deprecation"
  | "lifecycle:review"
  | "job:cancel"
  | "job:retry";

/** Response of GET /v1/auth/me. */
export interface CallerIdentity {
  subject_id: string;
  display_name: string;
  teams: string[];
  organization_member: boolean;
  governance_roles: string[];
  capabilities: Capability[];
}

/** Request body for POST /v1/discovery/recommend. */
export interface DiscoveryRequest {
  question: string;
  context?: string;
  filters?: Record<string, unknown>;
  max_recommendations?: number;
}

export interface DiscoveryRecommendation {
  resource_id: string;
  kind: string;
  version: string;
  artifact_hash: string;
  reason: string;
  score: number;
}

/**
 * Response of POST /v1/discovery/recommend.
 *
 * `answered: false` is a normal outcome. `considered` lists every candidate the
 * judge saw, so a refusal can be audited instead of taken on trust.
 */
export interface DiscoveryResponse {
  answered: boolean;
  recommendations: DiscoveryRecommendation[];
  refusal_reason: string | null;
  clarifying_questions: string[];
  considered: string[];
  degraded: string[];
  model: string | null;
}

/** Request body for POST /v1/registration/draft. */
export interface ManifestDraftRequest {
  description: string;
  kind_hint?: string;
}

export interface DraftValidation {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

/**
 * Response of POST /v1/registration/draft.
 *
 * Drifting a manifest writes nothing: `next_step` states that registering is a
 * separate action, and `manifest` may be null when drafting failed.
 */
export interface ManifestDraftResponse {
  manifest: Record<string, unknown> | null;
  validation: DraftValidation;
  warnings: string[];
  needs_input: string[];
  degraded: string[];
  model: string | null;
  next_step: string;
}

/** Request body for POST /v1/registration/preview. */
export interface DraftPreviewRequest {
  manifest: Record<string, unknown>;
}

/** Response of POST /v1/registration/preview. Reports, never writes. */
export interface DraftPreviewResponse {
  validation_valid: boolean;
  validation_errors: string[];
  base_present: boolean;
  existing_resource_id: string | null;
  existing_version: string | null;
  unified_diff: string;
  changed_paths: string[];
  added_paths: string[];
  removed_paths: string[];
  identical: boolean;
  will_write: boolean;
}

/** Request body for POST /v1/registration/commit. */
export interface DraftCommitRequest {
  manifest: Record<string, unknown>;
  reason: string;
  confirmed: boolean;
}

/** Response of POST /v1/registration/commit. The only call that writes. */
export interface DraftCommitResponse {
  resource_id: string;
  version_id: string;
  version: string;
  artifact_hash: string;
  slug: string;
  kind: string;
  created_status: string;
  next_step: string;
}


/** One runtime environment a resource can be exported to. */
export interface ExportTarget {
  identifier: string;
  display_name: string;
  skills_directory: string;
}

export interface ExportTargetList {
  items: ExportTarget[];
}

/**
 * A downloaded archive plus what the export could not carry.
 *
 * The warnings live in headers on the response, so they are captured here rather than
 * discarded with the binary: an export that silently loses a capability is exactly what
 * this feature must not do.
 */
export interface ExportDownload {
  blob: Blob;
  filename: string;
  digest: string;
  warningCodes: string[];
}

// ---------------------------------------------------------------- workflows

/** One resolved capability inside a run's frozen snapshot. */
export interface WorkflowSnapshotNode {
  node_id: string;
  stage_id: string;
  kind: string;
  slug: string;
  version: string;
  resource_version_id: string;
  artifact_hash: string;
  constraint: string;
  optional: boolean;
}

export interface WorkflowSnapshotStage {
  stage_id: string;
  title: string;
  gate: string;
  /** Node ids the stage owns. Present on a review; absent in a run's snapshot. */
  capabilities?: string[];
}

export interface WorkflowDefinition {
  workflow_id: string;
  resource_version_id: string;
  definition_schema_version: string;
  definition: {
    schema_version?: string;
    title?: string;
    stages?: WorkflowSnapshotStage[];
  };
  graph_hash: string;
  is_deterministic: boolean;
  created_at: string;
  updated_at: string;
  row_version: number;
}

/** A run as the API returns it, including the snapshot frozen at creation. */
export interface WorkflowRunSummary {
  run_id: string;
  workflow_id: string;
  status: string;
  requested_by: string;
  runtime: string;
  correlation_id: string;
  input_hash: string;
  output_hash: string | null;
  timeout_seconds: number;
  created_at: string;
  updated_at: string;
  row_version: number;
  approval_gate: string | null;
  approved_by: string | null;
  approval_reason: string | null;
  error_code: string | null;
  error_summary: string | null;
  resolution_snapshot_hash: string;
  resolution_snapshot: {
    nodes?: WorkflowSnapshotNode[];
    stages?: WorkflowSnapshotStage[];
    resolver_version?: string;
  };
}

/** One capability as the review reports it, with what it actually did. */
export interface ReviewCapability {
  node_id: string;
  kind: string;
  slug: string;
  stage_id: string;
  version: string;
  constraint: string;
  artifact_hash: string;
  optional: boolean;
  uses: number;
  succeeded: number;
  failed: number;
  other: number;
  output_hashes: string[];
  last_error: string | null;
}

export interface WorkflowRunReview {
  run_id: string;
  workflow_definition_id: string;
  status: string;
  requested_by: string;
  runtime: string;
  correlation_id: string;
  started_at: string | null;
  finished_at: string | null;
  definition_graph_hash: string;
  resolution_snapshot_hash: string;
  input_hash: string;
  output_hash: string | null;
  approval: {
    gate: string | null;
    approved_by: string | null;
    approved_at: string | null;
    reason: string | null;
  };
  error: { code: string | null; summary: string | null };
  stages: WorkflowSnapshotStage[];
  capabilities: ReviewCapability[];
  usage_rows: number;
  notes: string[];
  review_digest: string;
}
