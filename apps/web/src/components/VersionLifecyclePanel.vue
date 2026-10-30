<script setup lang="ts">
/**
 * Lifecycle transitions for one version.
 *
 * This is the screen that makes publishing possible at all: before it existed, the
 * server could move a version through its full lifecycle but no view ever called
 * the transition endpoint.
 *
 * Three rules shape the implementation:
 *
 * 1. **The offered buttons come from `domain/lifecycle.ts`**, whose table a Python
 *    test asserts against the server's own matrix. No edge is hardcoded here.
 * 2. **Every refusal is shown, not swallowed.** The server rejects transitions for
 *    reasons the UI cannot predict (an unapproved review, an unresolved dependency,
 *    a stale row version). Hiding that would make the platform feel broken.
 * 3. **A stale ETag is the common failure** when two people work on one version, so
 *    that case gets an explicit "reload" affordance rather than a generic error.
 */

import { computed, ref, watch } from "vue";
import { api } from "../api/client";
import type { ResourceVersion, ResourceVersionSummary } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";
import {
  EVERY_CAPABILITY,
  isSameState,
  stateLabel,
  transitionsFrom,
  versionEtag,
  type TransitionSpec,
} from "../domain/lifecycle";

const props = defineProps<{
  version: ResourceVersion | ResourceVersionSummary | null;
}>();

const emit = defineEmits<{ (event: "changed"): void }>();

const { capabilities, can } = useCapabilities();

const busy = ref<string | null>(null);
const error = ref("");
const notice = ref("");
const reason = ref("");
/** Set when the server rejects the ETag, so the user is told to reload. */
const stale = ref(false);

const currentStatus = computed(() => props.version?.status ?? "");

/** Buttons to offer for the current state, already filtered by capability. */
const options = computed<TransitionSpec[]>(() =>
  props.version ? transitionsFrom(props.version.status, capabilities.value) : [],
);

/**
 * Edges this state allows but the caller may not perform.
 *
 * Showing these separately turns a blank panel into an explanation. An owner
 * looking at a published version sees nothing they can do, and without this they
 * would reasonably conclude the feature is broken rather than that publishing is
 * someone else's job.
 */
const blocked = computed<TransitionSpec[]>(() => {
  if (!props.version) return [];
  const allowed = new Set(options.value.map((spec) => `${spec.target}:${spec.label}`));
  const all = transitionsFrom(props.version.status, new Set(EVERY_CAPABILITY));
  return all.filter((spec) => !allowed.has(`${spec.target}:${spec.label}`));
});

/** The distinct capabilities behind the blocked edges, for the hint text. */
const blockedCapabilities = computed(() =>
  [...new Set(blocked.value.map((spec) => spec.capability).filter(Boolean))].join(" / "),
);

/**
 * True when the caller holds no lifecycle capability at all.
 *
 * `resource:edit` is included because it unlocks the "back to draft" edge; without
 * it a reviewer-only caller would see the panel as writable and then be refused.
 */
const readOnly = computed(
  () =>
    !can("lifecycle:submit") &&
    !can("lifecycle:validate") &&
    !can("lifecycle:review") &&
    !can("lifecycle:publish") &&
    !can("lifecycle:reverse_deprecation") &&
    !can("resource:edit"),
);

/** Whether the edge needs a typed reason; the server rejects a blank one. */
function needsReason(spec: TransitionSpec): boolean {
  return spec.destructive || spec.target === "deprecated" || spec.target === "retired";
}

function describe(status: string): string {
  return stateLabel(status);
}

/**
 * Apply one transition.
 *
 * `body.row_version` and the `If-Match` header carry the same value by design: the
 * body is the domain-level expectation and the header is the transport-level one,
 * and the server checks both. Sending only one of them fails.
 */
async function apply(spec: TransitionSpec): Promise<void> {
  const version = props.version;
  if (!version || busy.value) return;

  const trimmed = reason.value.trim();
  if (needsReason(spec) && !trimmed) {
    error.value = `“${spec.label}”需要填写原因。`;
    return;
  }

  if (spec.destructive) {
    const confirmed = window.confirm(
      `确认执行“${spec.label}”？\n\n从 ${describe(version.status)} 变为 ${describe(spec.target)}。\n${spec.hint}`,
    );
    if (!confirmed) return;
  }

  busy.value = spec.label;
  error.value = "";
  notice.value = "";
  stale.value = false;
  try {
    const result = await api.transition(
      version.version_id,
      {
        target_status: spec.target,
        reason: trimmed || `${spec.label}（界面操作）`,
        row_version: version.row_version,
      },
      versionEtag(version.version_id, version.row_version),
    );

    const replay = isSameState(version.status, result.version.status);
    notice.value = replay
      ? `状态未变化（重放）：仍为 ${describe(result.version.status)}`
      : `已变为 ${describe(result.version.status)}` + (result.job ? `，已创建校验任务` : "");

    if (result.job) {
      notice.value += `（${result.job.job_id.slice(0, 8)}…）`;
    }
    reason.value = "";
    emit("changed");
  } catch (cause) {
    const text = cause instanceof Error ? cause.message : String(cause);
    error.value = text;
    // A 412 means someone else moved the version first. Say so plainly.
    if (/ETag|row_version|changed since/i.test(text) || /\b412\b/.test(text)) {
      stale.value = true;
    }
  } finally {
    busy.value = null;
  }
}

/** Clear transient messages when the version switches. */
watch(
  () => props.version?.version_id,
  () => {
    error.value = "";
    notice.value = "";
    stale.value = false;
    reason.value = "";
  },
);
</script>

<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h3 style="margin: 0">生命周期</h3>
      <span v-if="version" class="badge" :class="version.status">{{ describe(version.status) }}</span>
    </div>

    <p v-if="!version" class="muted">没有可操作的版本。</p>
    <template v-else>
      <p class="muted small">
        当前状态 <strong>{{ describe(version.status) }}</strong>。按钮来自服务端状态机，服务端仍会重新校验权限与证据。
      </p>

      <p v-if="readOnly" class="muted small">
        当前身份没有推进生命周期的权限，此面板为只读。
      </p>

      <template v-else>
        <div v-if="options.length === 0" class="muted small">
          <template v-if="blocked.length > 0">
            {{ describe(version.status) }} 状态可以推进，但当前身份缺少所需权限：
            <strong>{{ blocked.map((s) => s.label).join("、") }}</strong>。
            需要的权限为 <code>{{ blockedCapabilities }}</code>。
          </template>
          <template v-else>
            {{ describe(version.status) }} 状态没有可用的推进动作。
          </template>
        </div>

        <div v-else class="transition-grid">
          <div v-for="spec in options" :key="`${spec.target}-${spec.label}`" class="transition-item">
            <button
              :class="{ danger: spec.destructive }"
              :disabled="busy !== null"
              :title="spec.hint"
              @click="apply(spec)"
            >
              {{ busy === spec.label ? "处理中…" : spec.label }}
            </button>
            <span class="muted small">{{ spec.hint }}</span>
          </div>
        </div>

        <label class="reason">
          <span class="muted small">变更原因（退回、弃用、退役必填）</span>
          <input v-model="reason" type="text" placeholder="例如：依赖未评审完成" :disabled="busy !== null" />
        </label>
      </template>

      <p v-if="notice" class="ok">{{ notice }}</p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="stale" class="error small">
        该版本已被其他人修改，请刷新后再试。
        <button class="link" @click="emit('changed')">重新加载</button>
      </p>
    </template>
  </div>
</template>

<style scoped>
.transition-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
  margin: 10px 0;
}
.transition-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.reason {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}
.reason input {
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border, #334155);
  background: var(--surface, #0f172a);
  color: inherit;
}
.small {
  font-size: 12px;
}
.ok {
  color: var(--ok, #4ade80);
}
.link {
  background: none;
  border: none;
  padding: 0;
  color: var(--accent, #38bdf8);
  cursor: pointer;
  text-decoration: underline;
}
</style>
