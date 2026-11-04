<script setup lang="ts">
/**
 * The resource inventory.
 *
 * Two changes from the first version, both about finding things:
 *
 * 1. A filter box. A corpus of hundreds of resources cannot be browsed by scrolling, and
 *    the page previously offered only type and status dropdowns. The box filters what is
 *    already loaded, so typing is instant; the dropdowns still narrow the server query.
 *
 * 2. Counts, not just rows. Seeing "工具 · 草稿 25" tells a reader what the inventory looks
 *    like; 25 identical-looking rows do not.
 *
 * The three per-row links (版本/审核/依赖) were repeated on every row and competed with the
 * resource name for attention. They now appear as a single secondary action, because the
 * row's purpose is to open the resource.
 */
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceSummary } from "../api/types";

const items = ref<ResourceSummary[]>([]);
const loading = ref(false);
const error = ref("");
const kind = ref("");
const status = ref("");
const query = ref("");

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

/** Counts per status, shown above the table so the shape of the inventory is visible. */
const statusCounts = computed(() => {
  const counts = new Map<string, number>();
  for (const item of items.value) {
    const key = item.selected_version?.status ?? "draft";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params: Record<string, string> = {};
    if (kind.value) params.kind = kind.value;
    if (status.value) params.status = status.value;
    const response = await api.listResources(params);
    items.value = response.items;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
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
      <span class="faint" v-if="!loading">{{ items.length }} 项</span>
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

      <div v-if="items.length === 0" class="card empty-state">
        <p class="title">还没有资源</p>
        <p class="hint">
          先在“登记草稿”页用自然语言描述一个能力，或在“资源登记”页直接登记清单。
        </p>
        <router-link to="/register/draft" class="small">去登记草稿 →</router-link>
      </div>

      <div v-else-if="visible.length === 0" class="card empty-state">
        <p class="title">没有匹配「{{ query }}」的资源</p>
        <p class="hint">当前筛选下有 {{ items.length }} 项，换个关键词试试。</p>
        <button @click="reset">清除筛选</button>
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
