import type {
  AuditRecord,
  CallerIdentity,
  DiscoveryRequest,
  DiscoveryResponse,
  DraftCommitRequest,
  DraftCommitResponse,
  DraftPreviewRequest,
  DraftPreviewResponse,
  ErrorBody,
  Job,
  JobEvent,
  ListResponse,
  ManifestDraftRequest,
  ManifestDraftResponse,
  ResourceDetail,
  ResourceReview,
  ResourceSummary,
  ResourceVersion,
  SearchDiagnostics,
  SearchRequest,
  SearchResponse,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "/v1";
const TOKEN_KEY = "supercoder_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export class ApiError extends Error {
  code: string;
  status: number;
  traceId: string | null;

  constructor(body: ErrorBody, status: number) {
    super(body.detail ?? body.code);
    this.code = body.code;
    this.status = status;
    this.traceId = body.trace_id ?? null;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let body: ErrorBody = { code: "HTTP_ERROR", status: response.status };
    try {
      body = await response.json();
    } catch {
      /* keep default */
    }
    throw new ApiError(body, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Record<string, unknown>>("/health"),

  listResources: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request<ListResponse<ResourceSummary>>(`/resources${qs ? `?${qs}` : ""}`);
  },
  getResource: (id: string, includeVersions = false) =>
    request<ResourceDetail>(`/resources/${id}?include_versions=${includeVersions}`),
  createResource: (body: unknown) =>
    request<ResourceDetail>("/resources", { method: "POST", body: JSON.stringify(body) }),
  deleteResource: (id: string, reason: string) =>
    request<void>(`/resources/${id}`, {
      method: "DELETE",
      body: JSON.stringify({ reason }),
    }),

  listVersions: (resourceId: string) =>
    request<ListResponse<ResourceVersion>>(`/resources/${resourceId}/versions`),
  getVersion: (versionId: string) => request<ResourceVersion>(`/resource-versions/${versionId}`),
  diff: (versionId: string, baseVersionId: string) =>
    request<{ unified_diff: string; changed_paths: string[] }>(
      `/resource-versions/${versionId}/diff?base_version_id=${baseVersionId}`,
    ),
  rollback: (versionId: string, reason: string) =>
    request<ResourceVersion>(`/resource-versions/${versionId}/rollback`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  /**
   * Replace a draft body and/or patch its manifest.
   *
   * The server writes the file and hashes what it wrote, so no digest is sent: a
   * client-computed hash for a file it is about to change could only disagree.
   */
  editDraftBody: (
    versionId: string,
    body: { body?: string | null; manifest?: Record<string, unknown> | null; row_version: number; reason: string },
    etag: string,
  ) =>
    request<{ version: ResourceVersion; body_path: string; body_written: boolean }>(
      `/resource-versions/${versionId}/body`,
      {
        method: "PUT",
        body: JSON.stringify(body),
        headers: { "If-Match": etag },
      },
    ),
  readDraftBody: (versionId: string) =>
    request<{ version_id: string; entrypoint: string; body: string | null; available: boolean }>(
      `/resource-versions/${versionId}/body`,
    ),
  createVersion: (
    resourceId: string,
    body: { manifest: unknown; artifacts: unknown[]; replacement_version_id?: string | null; reason: string },
  ) =>
    request<ResourceVersion>(`/resources/${resourceId}/versions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  transition: (versionId: string, body: Record<string, unknown>, etag: string) =>
    request<{ version: ResourceVersion; job: Job | null; audit_id: string }>(
      `/resource-versions/${versionId}/transitions`,
      {
        method: "POST",
        body: JSON.stringify(body),
        headers: { "If-Match": etag },
      },
    ),
  listReviews: (versionId: string) =>
    request<{ items: ResourceReview[] }>(`/resource-versions/${versionId}/reviews`),
  createReview: (versionId: string, body: unknown) =>
    request<ResourceReview>(`/resource-versions/${versionId}/reviews`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listJobs: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request<ListResponse<Job>>(`/jobs${qs ? `?${qs}` : ""}`);
  },
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  listJobEvents: (jobId: string) =>
    request<{ items: JobEvent[] }>(`/jobs/${jobId}/events`),
  cancelJob: (jobId: string, reason: string, rowVersion: number, etag: string) =>
    request<Job>(`/jobs/${jobId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason, row_version: rowVersion }),
      headers: { "If-Match": etag },
    }),

  listAudit: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request<ListResponse<AuditRecord>>(`/audit${qs ? `?${qs}` : ""}`);
  },

  /**
   * Fetch the caller's identity and capabilities.
   *
   * The server resolves this from the bearer token alone, so it works while the
   * database is unavailable. Callers must still treat a failure as "no
   * capabilities": the console then renders read-only rather than guessing.
   */
  getCallerIdentity: () => request<CallerIdentity>("/auth/me"),

  /**
   * Search resources. Returns the response together with its diagnostic
   * headers: the frozen response body cannot carry degradation information
   * (additionalProperties:false), so it arrives in X-Search-Degraded.
   */
  searchResources: async (
    body: SearchRequest,
  ): Promise<{ data: SearchResponse; diagnostics: SearchDiagnostics }> => {
    const headers = new Headers({ Accept: "application/json", "Content-Type": "application/json" });
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${BASE}/search/resources`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let errorBody: ErrorBody = { code: "HTTP_ERROR", status: response.status };
      try {
        errorBody = await response.json();
      } catch {
        /* keep default */
      }
      throw new ApiError(errorBody, response.status);
    }
    const degradedHeader = response.headers.get("X-Search-Degraded") ?? "";
    const diagnostics: SearchDiagnostics = {
      degraded: degradedHeader ? degradedHeader.split(",").filter(Boolean) : [],
      indexRevision: response.headers.get("X-Index-Revision") ?? "",
    };
    return { data: (await response.json()) as SearchResponse, diagnostics };
  },

  /**
   * Ask "which capability should I use?" and receive either grounded
   * recommendations or an auditable refusal.
   *
   * A refusal is a normal outcome, not an error: the response carries the reason
   * and every candidate that was considered, so the console can show why nothing
   * fit rather than presenting a weak match as an answer.
   */
  recommend: (body: DiscoveryRequest) =>
    request<DiscoveryResponse>("/discovery/recommend", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /**
   * Draft a manifest candidate from a description.
   *
   * This writes nothing. The response carries the schema validation result and a
   * nextStep stating that registering is a separate action. The console must not
   * present a draft as a created resource.
   */
  draftManifest: (body: ManifestDraftRequest) =>
    request<ManifestDraftResponse>("/registration/draft", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /**
   * Ask what committing this draft would change. Writes nothing, so it is safe to
   * call from the review screen on every render.
   */
  previewDraft: (body: DraftPreviewRequest) =>
    request<DraftPreviewResponse>("/registration/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /**
   * Commit a reviewed draft. This is the only call in this client that creates a
   * resource, and it requires `confirmed: true` — the server refuses otherwise.
   */
  commitDraft: (body: DraftCommitRequest) =>
    request<DraftCommitResponse>("/registration/commit", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
