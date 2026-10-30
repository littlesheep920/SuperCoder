<script setup lang="ts">
/**
 * Audit log: what happened, who did it, and whether the chain holds.
 *
 * The audit trail is the reason a governance platform can be trusted after the
 * fact, so this screen does three things the API already supports but nothing
 * surfaced:
 *
 * 1. Shows the fields that matter for accountability: actor, action, outcome, and
 *    the status change a transition caused.
 * 2. Filters by actor, action, entity type, and outcome, plus the version id when
 *    embedded in a resource detail page.
 * 3. Checks the hash chain. Each entry carries `event_hash` and
 *    `previous_event_hash`; a break means the record was altered outside the
 *    application. Saying "chain intact" is only useful if the check is real, so a
 *    missing link is reported as a gap rather than silently ignored.
 */

import { computed, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { AuditRecord } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const props = defineProps<{
  /** Restrict to one resource version; set when embedded in a detail page. */
  versionId?: string | null;
  /** Show only the most recent N entries, for the embedded view. */
  compact?: boolean;
}>();

const { can } = useCapabilities();

const entries = ref<AuditRecord[]>([]);
const loading = ref(false);
const error = ref("");
const cursor = ref<string | null>(null);
const hasMore = ref(false);

const filterActor = ref("");
const filterAction = ref("");
const filterEntityType = ref("");
const filterOutcome = ref("");

const allowed = computed(() => can("audit:read"));

/** Distinct actions present in the loaded page, for the filter dropdown. */
const knownActions = computed(() =>
  [...new Set(entries.value.map((entry) => entry.action))].sort(),
);

/**
 * Chain check over the loaded window.
 *
 * `list_audit` returns newest first, so each entry's `previous_event_hash` should
 * equal the next entry's `event_hash`. The oldest entry in a page legitimately has
 * no successor, so the last comparison is skipped — otherwise every page would
 * report a false break.
 */
const chain = computed(() => {
  const items = entries.value;
  if (items.length < 2) return { checked: 0, breaks: 0 };
  let breaks = 0;
  for (let index = 0; index < items.length - 1; index += 1) {
    if (items[index].previous_event_hash !== items[index + 1].event_hash) {
      breaks += 1;
    }
  }
  return { checked: items.length - 1, breaks };
});

async function load(reset = true): Promise<void> {
  if (!allowed.value) return;
  loading.value = true;
  error.value = "";
  if (reset) {
    cursor.value = null;
  }
  try {
    const params: Record<string, string> = {
      limit: props.compact ? "10" : "50",
    };
    if (props.versionId) params.version_public_id = props.versionId;
    if (filterActor.value.trim()) params.actor_id = filterActor.value.trim();
    if (filterAction.value) params.action = filterAction.value;
    if (filterEntityType.value.trim()) params.entity_type = filterEntityType.value.trim();
    if (filterOutcome.value) params.outcome = filterOutcome.value;
    if (!reset && cursor.value) params.cursor = cursor.value;

    const page = await api.listAudit(params);
    const items = page.items ?? [];
    entries.value = reset ? items : [...entries.value, ...items];
    cursor.value = page.page?.next_cursor ?? null;
    hasMore.value = page.page?.has_more ?? false;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function clearFilters(): void {
  filterActor.value = "";
  filterAction.value = "";
  filterEntityType.value = "";
  filterOutcome.value = "";
  void load();
}

function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 12)}…` : "—";
}

/**
 * Load once the caller's capabilities are known.
 *
 * The capability set arrives asynchronously from the identity endpoint. Reading it
 * in `onMounted` sees an empty set, so `allowed` is false and the first load returns
 * immediately — the page then renders "0 entries" forever, which looks like an empty
 * audit log rather than a race. Watching `allowed` loads as soon as it becomes true,
 * and also reloads if the token changes to an identity with different rights.
 */
onMounted(() => {
  if (allowed.value) void load();
});

watch(allowed, (nowAllowed, wasAllowed) => {
  if (nowAllowed && !wasAllowed) void load();
});
</script>

<template>
  <div class="card" :class="{ compact }">
    <div class="row" style="justify-content: space-between">
      <h3 style="margin: 0">审计日志</h3>
      <span class="muted small">{{ entries.length }} 条</span>
    </div>

    <p v-if="!allowed" class="muted small">
      当前身份没有审计读取权限（需要 <code>audit:read</code>）。
    </p>

    <template v-else>
      <div v-if="!compact" class="filters">
        <label class="field">
          <span class="muted small">操作人</span>
          <input v-model="filterActor" placeholder="user-owner" @keyup.enter="load()" />
        </label>
        <label class="field">
          <span class="muted small">动作</span>
          <select v-model="filterAction" @change="load()">
            <option value="">全部</option>
            <option v-for="action in knownActions" :key="action" :value="action">{{ action }}</option>
          </select>
        </label>
        <label class="field">
          <span class="muted small">实体类型</span>
          <input v-model="filterEntityType" placeholder="resource_version" @keyup.enter="load()" />
        </label>
        <label class="field">
          <span class="muted small">结果</span>
          <select v-model="filterOutcome" @change="load()">
            <option value="">全部</option>
            <option value="succeeded">succeeded</option>
            <option value="failed">failed</option>
            <option value="rejected">rejected</option>
          </select>
        </label>
      </div>
      <div v-if="!compact" class="row" style="gap: 8px; margin: 8px 0">
        <button @click="load()" :disabled="loading">{{ loading ? "查询中…" : "查询" }}</button>
        <button @click="clearFilters" :disabled="loading">清除筛选</button>
      </div>

      <p v-if="loading && entries.length === 0" class="muted">加载中…</p>
      <p v-if="error" class="error">{{ error }}</p>

      <table v-if="entries.length > 0">
        <thead>
          <tr>
            <th>时间</th><th>操作人</th><th>动作</th><th>状态变化</th><th>结果</th><th>哈希</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in entries" :key="entry.audit_id">
            <td class="nowrap">{{ new Date(entry.occurred_at).toLocaleString() }}</td>
            <td>{{ entry.actor_id }}<span class="muted small"> · {{ entry.actor_type }}</span></td>
            <td>
              <span class="mono">{{ entry.action }}</span>
              <span v-if="entry.reason_code" class="muted small"> · {{ entry.reason_code }}</span>
            </td>
            <td>
              <template v-if="entry.from_status || entry.to_status">
                {{ entry.from_status ?? "—" }} → {{ entry.to_status ?? "—" }}
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <span :class="['outcome', entry.outcome]">{{ entry.outcome }}</span>
            </td>
            <td class="mono muted small" :title="entry.event_hash">{{ shortHash(entry.event_hash) }}</td>
          </tr>
        </tbody>
      </table>

      <p v-if="!loading && entries.length === 0 && !error" class="muted small">没有匹配的审计记录。</p>

      <p v-if="chain.checked > 0" :class="chain.breaks === 0 ? 'ok small' : 'error small'">
        <template v-if="chain.breaks === 0">
          哈希链连续：本页校验了 {{ chain.checked }} 对相邻记录，链接完整。
        </template>
        <template v-else>
          哈希链在这 {{ chain.checked }} 对中有 {{ chain.breaks }} 处断链，说明记录可能被应用之外改动。
        </template>
      </p>

      <button v-if="hasMore" @click="load(false)" :disabled="loading" style="margin-top: 8px">
        {{ loading ? "加载中…" : "加载更多" }}
      </button>
    </template>
  </div>
</template>

<style scoped>
.filters {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 10px;
  margin: 10px 0;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field input,
.field select {
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border, #334155);
  background: var(--surface, #0f172a);
  color: inherit;
  font: inherit;
}
.compact h3 {
  font-size: 14px;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.small {
  font-size: 12px;
}
.nowrap {
  white-space: nowrap;
}
.ok {
  color: var(--ok, #4ade80);
}
.outcome.succeeded {
  color: var(--ok, #4ade80);
}
.outcome.failed,
.outcome.rejected {
  color: #f87171;
}
</style>
