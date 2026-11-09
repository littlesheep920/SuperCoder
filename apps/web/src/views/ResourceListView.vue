<script setup lang="ts">
/**
 * The resource inventory.
 *
 * The page reads `GET /v1/resources` page by page. It used to request one page of the
 * server's default 25 and stop, while the header said "25 项" as if that were the whole
 * inventory: the other resources could not be reached from here, and the status counts and the
 * filter box silently covered those 25 only. Now:
 *
 * 1. Pages of 100 (the server's maximum) are requested with the cursor the previous page
 *    returned; "加载更多" fetches the next one until the server says there is none.
 * 2. A total ("共 N 项") is stated only when every page is loaded. Otherwise the header says
 *    how many are loaded and that more exist, and the counts and the filter say they cover the
 *    loaded part only.
 * 3. Changing a dropdown restarts from the first page. A response that arrives after the query
 *    changed is dropped, so a slow first page can never overwrite a newer one.
 *
 * The filter box filters what is loaded, so typing is instant; the dropdowns narrow the
 * server query. The per-row links (版本/审核/依赖) appear on the detail page instead: the
 * row's purpose is to open the resource.
 */
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceSummary } from "../api/types";

/** The largest page `GET /v1/resources` accepts (`limit` ≤ 100). */
const PAGE_SIZE = 100;

const items = ref<ResourceSummary[]>([]);
const nextCursor = ref<string | null>(null);
const loading = ref(false);
const loadingMore = ref(false);
const error = ref("");
const moreError = ref("");
const kind = ref("");
const status = ref("");
const query = ref("");

/** Bumped by every new query; a response carrying an older value is discarded. */
let generation = 0;

const KINDS = ["skill", "mcp", "prompt", "agent", "workflow", "tool"];
const STATUSES = [
  "draft",
  "validating",
  "pending_review",
  "approved",
  "published",
  "deprecated",
  "retired",
];

const hasMore = computed(() => nextCursor.value !== null);

/** Case-insensitive match over slug and team: the two fields a reader scans by. */
const visible = computed(() => {
  const needle = query.value.trim().toLowerCase();
  if (!needle) return items.value;
  return items.value.filter(
    (item) =>
      item.slug.toLowerCase().includes(needle) ||
      (item.owner_team ?? "").toLowerCase().includes(needle),
  );
});

/** Counts per status over the loaded resources, shown above the table. */
const statusCounts = computed(() => {
  const counts = new Map<string, number>();
  for (const item of items.value) {
    const key = item.selected_version?.status ?? "draft";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
});

function pageParams(cursor: string | null): Record<string, string> {
  const params: Record<string, string> = { limit: String(PAGE_SIZE) };
  if (kind.value) params.kind = kind.value;
  if (status.value) params.status = status.value;
  if (cursor) params.cursor = cursor;
  return params;
}

/** Start over from the first page with the current dropdown values. */
async function load() {
  const mine = ++generation;
  loading.value = true;
  loadingMore.value = false;
  error.value = "";
  moreError.value = "";
  try {
    const response = await api.listResources(pageParams(null));
    if (mine !== generation) return;
    items.value = response.items;
    nextCursor.value = response.page?.next_cursor ?? null;
  } catch (e) {
    if (mine !== generation) return;
    items.value = [];
    nextCursor.value = null;
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (mine === generation) loading.value = false;
  }
}

/** Append the next page. Keeps what is loaded if the request fails. */
async function loadMore() {
  if (!nextCursor.value || loadingMore.value) return;
  const mine = generation;
  loadingMore.value = true;
  moreError.value = "";
  try {
    const response = await api.listResources(pageParams(nextCursor.value));
    if (mine !== generation) return;
    items.value = [...items.value, ...response.items];
    nextCursor.value = response.page?.next_cursor ?? null;
  } catch (e) {
    if (mine !== generation) return;
    moreError.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (mine === generation) loadingMore.value = false;
  }
}

function reset() {
  query.value = "";
  kind.value = "";
  status.value = "";
  load();
}

onMounted(load);
</script>

<template>
  <div>
    <div class="row" style="margin-bottom: var(--space-4)">
      <h2>能力资产中心</h2>
      <template v-if="!loading && !error">
        <span v-if="hasMore" class="faint">已加载 {{ items.length }} 项，还有更多</span>
        <span v-else class="faint">共 {{ items.length }} 项</span>
      </template>
    </div>

    <div class="row" style="margin-bottom: var(--space-3)">
      <input
        v-model="query"
        placeholder="按名称或团队筛选…"
        style="width: 260px"
        aria-label="筛选资源"
      />
      <select v-model="kind" @change="load" style="width: 150px" aria-label="资源类型">
        <option value="">全部类型</option>
        <option v-for="value in KINDS" :key="value" :value="value">{{ value }}</option>
      </select>
      <select v-model="status" @change="load" style="width: 170px" aria-label="生命周期状态">
        <option value="">全部状态</option>
        <option v-for="value in STATUSES" :key="value" :value="value">{{ value }}</option>
      </select>
      <button @click="load">刷新</button>
      <button v-if="query || kind || status" @click="reset">清除筛选</button>
    </div>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">
      {{ error }}
      <button class="link" @click="load">重试</button>
    </p>

    <template v-else>
      <div v-if="statusCounts.length" class="stat" style="margin-bottom: var(--space-2)">
        <div v-for="[name, count] in statusCounts" :key="name">
          <span class="value">{{ count }}</span>
          <span class="label">{{ name }}</span>
        </div>
      </div>
      <p v-if="hasMore && statusCounts.length" class="faint" style="margin-bottom: var(--space-3)">
        以上计数和筛选只覆盖已加载的 {{ items.length }} 项。
      </p>

      <div v-if="items.length === 0" class="card empty-state">
        <p class="title">还没有资源</p>
        <p class="hint">
          先在“登记草稿”页用自然语言描述一个能力，或在“资源登记”页直接登记清单。
        </p>
        <router-link to="/register/draft" class="small">去登记草稿</router-link>
      </div>

      <div v-else-if="visible.length === 0" class="card empty-state">
        <p class="title" v-if="hasMore">已加载的 {{ items.length }} 项中没有匹配「{{ query }}」的资源</p>
        <p class="title" v-else>没有匹配「{{ query }}」的资源</p>
        <p class="hint" v-if="hasMore">还有资源没有加载。加载更多后，筛选会继续覆盖新加载的部分。</p>
        <p class="hint" v-else>当前筛选下有 {{ items.length }} 项，换个关键词试试。</p>
        <div class="row">
          <button v-if="hasMore" @click="loadMore" :disabled="loadingMore">
            {{ loadingMore ? "加载中…" : "加载更多" }}
          </button>
          <button @click="reset">清除筛选</button>
        </div>
      </div>

      <table v-else>
        <thead>
          <tr>
            <th>名称</th>
            <th style="width: 110px">类型</th>
            <th style="width: 90px">版本</th>
            <th style="width: 150px">状态</th>
            <th>团队</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in visible" :key="item.resource_id">
            <td>
              <router-link :to="`/resources/${item.resource_id}`" class="mono">
                {{ item.slug }}
              </router-link>
            </td>
            <td class="faint">{{ item.kind }}</td>
            <td class="mono">{{ item.selected_version?.version ?? "—" }}</td>
            <td>
              <span class="badge" :class="item.selected_version?.status ?? 'draft'">
                {{ item.selected_version?.status ?? "draft" }}
              </span>
            </td>
            <td class="muted">{{ item.owner_team }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="hasMore && visible.length > 0" class="row" style="margin-top: var(--space-3)">
        <button @click="loadMore" :disabled="loadingMore">
          {{ loadingMore ? "加载中…" : "加载更多" }}
        </button>
        <span class="faint">每次 {{ PAGE_SIZE }} 项</span>
      </div>
      <p v-if="moreError" class="error">
        加载下一页失败：{{ moreError }}
        <button class="link" @click="loadMore">重试</button>
      </p>
    </template>
  </div>
</template>

<style scoped>
button.link {
  background: none;
  border: none;
  color: var(--info);
  padding: 0;
  font-size: 12.5px;
  text-decoration: underline;
}
</style>
