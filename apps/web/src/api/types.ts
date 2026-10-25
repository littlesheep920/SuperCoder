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
  action: string;
  outcome: string;
  entity_type: string;
  entity_public_id: string;
  reason_code: string;
  reason_summary: string;
  event_hash: string;
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
