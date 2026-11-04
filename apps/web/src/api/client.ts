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
  ExportDownload,
  ExportTargetList,
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
  WorkflowDefinition,
  WorkflowRunReview,
  WorkflowRunSummary,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "/v1";
const TOKEN_KEY = "supercoder_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

/**
 * Translate a server `detail` into something a reader can act on.
 *
 * The API speaks in English codes and gate names (`an independent valid approval
 * review is required`, `dependency unresolved`). Those are precise for logs and useless
 * for a person looking at a Chinese console, and surfacing them verbatim made a
 * governance rule look like a crash. Everything that does not match a known phrase
 * passes through unchanged — hiding an unknown message would be worse than showing it.
 */
export function explainServerDetail(detail: string, status: number): string {
  const value = detail.toLowerCase();

  const rules: Array<[RegExp, string]> = [
    [
      /independent valid approval/,
      "还不能审核通过：需要先有一条独立审核记录（在“审核队列”里批准当前内容）。",
    ],
    [
      /validation (is )?(not )?(passed|complete)|validation required/,
      "校验尚未完成，无法进入待审核。",
    ],
    [/dependency .*unresolved|unresolved .*dependency/, "存在未解析的必需依赖，无法继续。"],
    [/at least one runtime/, "发布前至少要声明一个运行环境。"],
    [/owner role/, "此操作需要所有者角色。"],
    [/publisher/, "此操作需要发布者角色。"],
    [/reviewer/, "此操作需要审核角色。"],
    [/row_version|etag|changed since/, "这条记录已被其他人修改，请刷新后重试。"],
    [/version already exists/, "该版本号已存在，请换一个版本号。"],
    [/only draft versions can be modified/, "只有草稿状态可以修改；请先退回草稿。"],
    [/status has already been retired|retired/, "该版本已退役，不能再变更。"],
    [/resource not found or not visible/, "找不到该资源，或无权查看。"],
    [/version not found/, "找不到该版本。"],
    [/artifact .* not present|provenance/, "提交的产物与磁盘上的文件不一致。"],
    [/rate limit|too many requests/, "请求过于频繁，请稍后再试。"],
  ];

  for (const [pattern, message] of rules) {
    if (pattern.test(value)) return message;
  }

  if (status === 401) return "未登录或令牌无效，请在右上角填写访问令牌。";
  if (status === 403) return "当前身份没有执行此操作的权限。";
  if (status === 404) return "请求的资源不存在。";
  if (status === 409) return "操作与当前状态冲突，请刷新后重试。";
  if (status === 412) return "这条记录已被其他人修改，请刷新后重试。";
  if (status === 422) return `提交的内容未通过校验：${detail}`;
  if (status === 503) return "依赖的服务暂不可用，请稍后重试。";
  return detail;
}

export class ApiError extends Error {
  code: string;
  status: number;
  traceId: string | null;
  /** The server's original message, kept for logs and troubleshooting. */
  serverDetail: string;

  constructor(body: ErrorBody, status: number) {
    const raw = body.detail ?? body.code;
    super(explainServerDetail(raw, status));
    this.serverDetail = raw;
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
  /** Requeue a failed job. Reserves 202: acceptance is not completion. */
  retryJob: (jobId: string) =>
    request<Job>(`/jobs/${jobId}/retry`, { method: "POST" }),

  /**
   * Relative path for the event stream.
   *
   * `EventSource` cannot send an Authorization header, so this relies on the same
   * origin that already serves `/v1`. The token is deliberately not appended as a
   * query parameter: URLs are recorded by proxies and kept in history, so a token
   * there is a leaked token. If the deployment requires header auth for streams,
   * the proxy must inject it.
   */
  jobEventsStreamPath: (jobId: string) => `${BASE}/jobs/${jobId}/events/stream`,

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

  listExportTargets: () => request<ExportTargetList>("/export-targets"),

  /**
   * Download a published version for one environment.
   *
   * This bypasses `request` on purpose: the response is a zip, not JSON, and the useful
   * metadata lives in headers. Returning `{blob, filename, warnings, digest}` lets the
   * caller save the file and still show what the export could not carry.
   */
  exportVersion: async (
    versionId: string,
    target: string,
  ): Promise<ExportDownload> => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(
      `${BASE}/resource-versions/${versionId}/export?target=${encodeURIComponent(target)}`,
      { headers },
    );
    if (!response.ok) {
      let body: ErrorBody = { code: "HTTP_ERROR", status: response.status };
      try {
        body = await response.json();
      } catch {
        // A non-JSON body means the failure is not one of ours; the status still tells
        // the story, and the translated fallback for that status is better than nothing.
      }
      throw new ApiError(body, response.status);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    return {
      blob,
      filename: match?.[1] ?? `${target}.zip`,
      digest: response.headers.get("X-Export-Digest") ?? "",
      warningCodes: (response.headers.get("X-Export-Warnings") ?? "")
        .split(",")
        .map((code) => code.trim())
        .filter(Boolean),
    };
  },

  /** Serve a downloaded archive to the browser as a file save. */
  saveDownload: (download: ExportDownload) => {
    const url = URL.createObjectURL(download.blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = download.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    // Revoking immediately can cancel the download in some browsers; one turn later is
    // enough for the save to start and still frees the blob.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  },

  // ---------------------------------------------------------------- workflows

  listWorkflows: () => request<ListResponse<WorkflowDefinition>>("/workflows"),

  getWorkflow: (workflowId: string) =>
    request<WorkflowDefinition>(`/workflows/${workflowId}`),

  createRun: (
    workflowId: string,
    body: { runtime: string; input: Record<string, unknown>; timeout_seconds: number },
    idempotencyKey: string,
  ) =>
    request<WorkflowRunSummary>(`/workflows/${workflowId}/runs`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(body),
    }),

  listRuns: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request<ListResponse<WorkflowRunSummary>>(`/workflow-runs${qs ? `?${qs}` : ""}`);
  },

  getRun: (runId: string) => request<WorkflowRunSummary>(`/workflow-runs/${runId}`),

  getRunReview: (runId: string) =>
    request<WorkflowRunReview>(`/workflow-runs/${runId}/review`),

  decideRunApproval: (
    runId: string,
    body: { gate: string; decision: "approve" | "reject"; reason: string; row_version: number },
  ) =>
    request<WorkflowRunSummary>(`/workflow-runs/${runId}/approvals`, {
      method: "POST",
      // The If-Match header must agree with the body's row version; the server checks both.
      headers: { "If-Match": `"${runId}:${body.row_version}"` },
      body: JSON.stringify(body),
    }),

};
