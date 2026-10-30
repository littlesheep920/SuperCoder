/**
 * The lifecycle state machine, mirrored for display.
 *
 * Why this file exists
 * --------------------
 * The console must show exactly the transitions the server will accept. The
 * temptation is to hardcode "from draft you can go to validating" in a component;
 * that creates a second copy of the rule, and the copies drift. The failure is
 * quiet in both directions: a button that renders and is then rejected, or a legal
 * transition never offered.
 *
 * So the table lives here, once, and `tests/test_ui_lifecycle_parity.py` asserts it
 * against the Python state machine. If the server matrix changes, that test fails
 * rather than the UI silently lying.
 *
 * What this is NOT: an authorization decision. The server checks gates, roles and
 * evidence at the moment a transition runs. This table only decides which buttons
 * to offer; every click still goes to the server and can still be refused.
 */

export type LifecycleState =
  | "draft"
  | "validating"
  | "pending_review"
  | "approved"
  | "published"
  | "deprecated"
  | "retired";

/**
 * Permitted edges, including same-state replay.
 *
 * Mirrors `allowed_targets()` in apps/api/domain/lifecycle.py exactly, including
 * the self-edges: a same-state request replays an already committed result and
 * must not be filtered out by the UI, because the server treats it as valid.
 */
export const ALLOWED_TARGETS: Readonly<Record<LifecycleState, readonly LifecycleState[]>> = {
  draft: ["draft", "validating", "retired"],
  validating: ["validating", "draft", "pending_review", "retired"],
  pending_review: ["pending_review", "approved", "draft", "retired"],
  approved: ["approved", "published", "draft", "retired"],
  published: ["published", "deprecated"],
  deprecated: ["deprecated", "published", "retired"],
  retired: ["retired"],
};

/** Human labels. Kept next to the states so a new state cannot be added unlabelled. */
export const STATE_LABELS: Readonly<Record<LifecycleState, string>> = {
  draft: "草稿",
  validating: "校验中",
  pending_review: "待审核",
  approved: "已批准",
  published: "已发布",
  deprecated: "已弃用",
  retired: "已退役",
};

/** Capability a caller needs before the server will accept the edge. */
export type TransitionCapability =
  | "lifecycle:submit"
  | "lifecycle:validate"
  | "lifecycle:review"
  | "lifecycle:publish"
  | "lifecycle:reverse_deprecation"
  | "resource:edit";

export interface TransitionSpec {
  target: LifecycleState;
  label: string;
  /** null when the server performs no extra capability check beyond ownership. */
  capability: TransitionCapability | null;
  /** Shown before the click so a refusal is not a surprise. */
  hint: string;
  /** Destructive edges are styled differently and asked for confirmation. */
  destructive: boolean;
}

/**
 * Edges worth offering, with the capability the governance service checks.
 *
 * Same-state edges are deliberately excluded from this list: they exist in the
 * matrix for replay semantics, but a "re-apply current state" button would be
 * noise. `isSameState` keeps them distinguishable where it matters.
 */
const SPECS: readonly TransitionSpec[] = [
  {
    target: "validating",
    label: "提交校验",
    capability: "lifecycle:submit",
    hint: "冻结当前候选内容并创建一个校验任务。",
    destructive: false,
  },
  {
    target: "pending_review",
    label: "校验通过",
    capability: "lifecycle:validate",
    hint: "需要校验者角色，且校验结果通过。",
    destructive: false,
  },
  {
    target: "pending_review",
    label: "校验未通过并退回",
    capability: "lifecycle:validate",
    hint: "需要校验者角色，附带校验失败证据。",
    destructive: true,
  },
  {
    target: "approved",
    label: "审核通过",
    capability: "lifecycle:review",
    hint: "需要一位独立审核者针对当前内容哈希给出批准。",
    destructive: false,
  },
  {
    target: "published",
    label: "发布",
    capability: "lifecycle:publish",
    hint: "需要发布者角色，且已声明至少一个运行环境、依赖全部可解析。",
    destructive: false,
  },
  {
    target: "deprecated",
    label: "标记弃用",
    capability: "lifecycle:publish",
    hint: "需要填写弃用原因。",
    destructive: false,
  },
  {
    target: "draft",
    label: "退回草稿",
    capability: "resource:edit",
    hint: "回到草稿后可继续编辑正文。",
    destructive: true,
  },
  {
    target: "retired",
    label: "退役",
    capability: null,
    hint: "需要填写退役原因。退役后不可恢复。",
    destructive: true,
  },
];

/** Every capability a transition edge can require. */
export const EVERY_CAPABILITY: ReadonlySet<TransitionCapability> = new Set<TransitionCapability>([
  "lifecycle:submit",
  "lifecycle:validate",
  "lifecycle:review",
  "lifecycle:publish",
  "lifecycle:reverse_deprecation",
  "resource:edit",
]);

/** States a caller may move to from `state`, in display order. */
export function targetsFrom(state: string): readonly LifecycleState[] {
  const key = state as LifecycleState;
  return ALLOWED_TARGETS[key] ?? [];
}

/**
 * Offerable edges from `state`.
 *
 * `validating` -> `pending_review` appears twice with different labels (pass and
 * fail); both are legal and both need distinguishing evidence, so both are offered
 * and the caller picks. Everything else maps one-to-one.
 */
export function transitionsFrom(state: string, held: ReadonlySet<string>): TransitionSpec[] {
  const allowed = new Set(targetsFrom(state));
  return SPECS.filter((spec) => {
    if (!allowed.has(spec.target)) return false;
    // A "back to draft" button on an already-draft version does nothing visible;
    // the server would treat it as a replay. Offer it only when it changes state.
    if (spec.target === "draft" && state === "draft") return false;
    if (spec.capability === null) return true;
    return held.has(spec.capability);
  });
}

/** True when `from` -> `to` is a same-state replay rather than a real move. */
export function isSameState(from: string, to: string): boolean {
  return from === to;
}

/** Whether the state machine permits the edge at all, ignoring capabilities. */
export function isAllowed(from: string, to: string): boolean {
  return targetsFrom(from).includes(to as LifecycleState);
}

/** Label for a state code, falling back to the raw code if unknown. */
export function stateLabel(state: string): string {
  return STATE_LABELS[state as LifecycleState] ?? state;
}

/**
 * ETag for a version, matching `make_etag` on the server:
 * `"<public_id>:<row_version>"` including the quotes.
 */
export function versionEtag(publicId: string, rowVersion: number): string {
  return `"${publicId}:${rowVersion}"`;
}
