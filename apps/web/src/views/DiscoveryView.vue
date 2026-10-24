<script setup lang="ts">
/**
 * Capability discovery console: filters | results | explanation, plus a debug
 * strip. Layout follows PRD §5.2 so the three questions a seeker actually has
 * are answered side by side: what can I narrow on, what did I get, and why.
 */
import { computed, ref } from "vue";
import { api } from "../api/client";
import type { RankingProfile, SearchDiagnostics, SearchHit, SearchSort } from "../api/types";

const KINDS = ["skill", "mcp", "prompt", "agent", "workflow", "tool"] as const;
const STATUSES = ["draft", "validating", "pending_review", "approved", "published"] as const;

const query = ref("");
const selectedKinds = ref<string[]>([]);
const selectedStatuses = ref<string[]>([]);
const runtime = ref("");
const tag = ref("");
const profile = ref<RankingProfile>("hybrid");
const sort = ref<SearchSort>("relevance");
const limit = ref(20);

const loading = ref(false);
const error = ref("");
const hits = ref<SearchHit[]>([]);
const took = ref(0);
const queryId = ref("");
const diagnostics = ref<SearchDiagnostics>({ degraded: [], indexRevision: "" });
const selected = ref<SearchHit | null>(null);
const searched = ref(false);

const showEmpty = computed(() => searched.value && !loading.value && !error.value && hits.value.length === 0);

const DEGRADATION_LABELS: Record<string, string> = {
  semantic_unavailable: "语义检索未启用（使用关键词兜底）",
  semantic_error: "语义检索调用失败（已降级为关键词）",
  semantic_fallback_to_keyword: "语义无结果，已回退关键词",
  rerank_unavailable: "精排未启用",
  rerank_error: "精排调用失败",
};

function toggle(list: string[], value: string) {
  const index = list.indexOf(value);
  if (index >= 0) list.splice(index, 1);
  else list.push(value);
}

function buildFilters() {
  return {
    kinds: selectedKinds.value.length ? [...selectedKinds.value] : undefined,
    statuses: selectedStatuses.value.length ? [...selectedStatuses.value] : undefined,
    runtimes: runtime.value ? [runtime.value] : undefined,
    tags: tag.value ? [tag.value] : undefined,
  };
}

async function runSearch() {
  if (!query.value.trim()) {
    error.value = "请输入要查找的能力描述";
    return;
  }
  loading.value = true;
  error.value = "";
  searched.value = true;
  selected.value = null;
  try {
    const { data, diagnostics: headers } = await api.searchResources({
      query: query.value,
      filters: buildFilters(),
      limit: limit.value,
      ranking_profile: profile.value,
      sort: sort.value,
      include_highlights: true,
    });
    hits.value = data.items;
    took.value = data.took_ms;
    queryId.value = data.query_id;
    diagnostics.value = headers;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    hits.value = [];
  } finally {
    loading.value = false;
  }
}

function reset() {
  query.value = "";
  selectedKinds.value = [];
  selectedStatuses.value = [];
  runtime.value = "";
  tag.value = "";
  profile.value = "hybrid";
  sort.value = "relevance";
  hits.value = [];
  selected.value = null;
  searched.value = false;
  error.value = "";
  diagnostics.value = { degraded: [], indexRevision: "" };
}

function componentEntries(hit: SearchHit): [string, number][] {
  return Object.entries(hit.score_components ?? {});
}

function scorePercent(value: number): string {
  return (value * 100).toFixed(1) + "%";
}
</script>

<template>
  <div class="discovery">
    <header class="row" style="margin-bottom: 12px">
      <h2 style="margin: 0">能力发现台</h2>
      <input
        v-model="query"
        placeholder="用业务语言描述你的需求，例如：审查代码改动并给出证据"
        style="flex: 1"
        @keyup.enter="runSearch"
      />
      <select v-model="profile" style="width: 130px" aria-label="检索模式">
        <option value="hybrid">混合</option>
        <option value="keyword">关键词</option>
        <option value="semantic">语义</option>
      </select>
      <select v-model="sort" style="width: 110px" aria-label="排序方式">
        <option value="relevance">相关度</option>
        <option value="newest">最新</option>
        <option value="name">名称</option>
      </select>
      <button class="primary" :disabled="loading" @click="runSearch">
        {{ loading ? "检索中…" : "检索" }}
      </button>
      <button @click="reset">重置</button>
    </header>

    <div class="columns">
      <!-- Left: structured filters -->
      <aside class="panel" aria-label="结构化筛选">
        <h3>筛选</h3>
        <fieldset>
          <legend>资源类型</legend>
          <label v-for="kind in KINDS" :key="kind" class="check">
            <input
              type="checkbox"
              :checked="selectedKinds.includes(kind)"
              @change="toggle(selectedKinds, kind)"
            />
            {{ kind }}
          </label>
        </fieldset>
        <fieldset>
          <legend>生命周期</legend>
          <label v-for="status in STATUSES" :key="status" class="check">
            <input
              type="checkbox"
              :checked="selectedStatuses.includes(status)"
              @change="toggle(selectedStatuses, status)"
            />
            {{ status }}
          </label>
        </fieldset>
        <label class="field">
          运行环境
          <input v-model="runtime" placeholder="如 kiro" />
        </label>
        <label class="field">
          标签
          <input v-model="tag" placeholder="如 code-review" />
        </label>
        <label class="field">
          返回条数
          <input v-model.number="limit" type="number" min="1" max="100" />
        </label>
        <p class="muted hint">筛选在检索前生效，无权限的资源不会出现在任何结果中。</p>
      </aside>

      <!-- Center: results -->
      <section class="panel results" aria-label="检索结果">
        <h3>
          结果
          <span v-if="searched && !loading" class="muted small">
            {{ hits.length }} 项 · {{ took }} ms
          </span>
        </h3>

        <p v-if="loading" class="muted">检索中…</p>
        <p v-else-if="error" class="error">{{ error }}</p>
        <p v-else-if="showEmpty" class="muted">
          没有匹配的能力。可尝试放宽筛选，或换用更概括的描述。
        </p>
        <p v-else-if="!searched" class="muted">输入需求开始检索。</p>

        <ul v-else class="hit-list">
          <li
            v-for="hit in hits"
            :key="hit.version.version_id"
            class="hit"
            :class="{ active: selected?.rank === hit.rank }"
            @click="selected = hit"
          >
            <div class="hit-head">
              <span class="rank">#{{ hit.rank }}</span>
              <strong>{{ hit.resource.slug }}</strong>
              <span class="badge">{{ hit.resource.kind }}</span>
              <span class="badge" :class="hit.version.status">{{ hit.version.status }}</span>
              <span class="muted small">v{{ hit.version.version }}</span>
            </div>
            <p class="hit-desc">{{ hit.highlights[0] ?? "（无摘要）" }}</p>
            <div class="hit-meta muted small">
              <span>相关度 {{ scorePercent(hit.score) }}</span>
              <span v-if="hit.matched_fields.length">
                命中字段：{{ hit.matched_fields.join(" / ") }}
              </span>
              <span>团队 {{ hit.resource.owner_team }}</span>
            </div>
          </li>
        </ul>
      </section>

      <!-- Right: explanation -->
      <aside class="panel" aria-label="推荐解释">
        <h3>解释</h3>
        <p v-if="!selected" class="muted">
          选择左侧任一结果，查看它为什么被召回。
        </p>
        <div v-else>
          <h4>{{ selected.resource.slug }}</h4>
          <p class="muted small">
            {{ selected.resource.kind }} · v{{ selected.version.version }} ·
            {{ selected.resource.owner_team }}
          </p>

          <h5>各路分数</h5>
          <table class="scores">
            <tbody>
              <tr v-for="[name, value] in componentEntries(selected)" :key="name">
                <th>{{ name }}</th>
                <td>{{ value }}</td>
              </tr>
              <tr v-if="componentEntries(selected).length === 0">
                <td class="muted" colspan="2">无分数明细</td>
              </tr>
            </tbody>
          </table>

          <h5>命中字段</h5>
          <p class="small">
            <span v-if="!selected.matched_fields.length" class="muted">无（可能由语义召回）</span>
            <span v-for="field in selected.matched_fields" :key="field" class="chip">{{ field }}</span>
          </p>

          <h5>证据</h5>
          <dl class="evidence">
            <dt>内容哈希</dt>
            <dd class="mono">{{ selected.source_artifact_hash }}</dd>
            <dt>索引版本</dt>
            <dd class="mono">{{ selected.index_revision ?? "—" }}</dd>
          </dl>

          <p>
            <router-link :to="`/resources/${selected.resource.resource_id}`">
              查看资源详情 →
            </router-link>
          </p>
        </div>
      </aside>
    </div>

    <!-- Bottom: debug strip -->
    <section class="debug panel" aria-label="检索调试">
      <h3>检索调试</h3>
      <div class="debug-grid">
        <div><span class="muted">查询 ID</span><br /><span class="mono small">{{ queryId || "—" }}</span></div>
        <div><span class="muted">索引版本</span><br /><span class="mono small">{{ diagnostics.indexRevision || "—" }}</span></div>
        <div><span class="muted">耗时</span><br /><span class="mono small">{{ took }} ms</span></div>
        <div><span class="muted">模式</span><br /><span class="mono small">{{ profile }}</span></div>
      </div>
      <div v-if="diagnostics.degraded.length" class="degraded">
        <strong>降级提示：</strong>
        <ul>
          <li v-for="reason in diagnostics.degraded" :key="reason">
            {{ DEGRADATION_LABELS[reason] ?? reason }}
          </li>
        </ul>
        <p class="muted small">
          降级代表本次结果的召回路径不完整，分数不可直接与其他模式比较。
        </p>
      </div>
      <p v-else-if="searched" class="muted small">所有检索阶段正常，无降级。</p>
    </section>
  </div>
</template>

<style scoped>
.discovery {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.columns {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr) 300px;
  gap: 12px;
  align-items: start;
}
@media (max-width: 1100px) {
  .columns {
    grid-template-columns: 1fr;
  }
}
.panel {
  border: 1px solid var(--border, #d8dee9);
  border-radius: 6px;
  padding: 12px;
}
.panel h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
fieldset {
  border: 0;
  margin: 0 0 10px;
  padding: 0;
}
legend {
  font-size: 12px;
  color: #667;
  padding: 0;
}
.check {
  display: block;
  font-size: 13px;
  margin: 2px 0;
}
.field {
  display: block;
  font-size: 12px;
  margin-bottom: 8px;
}
.field input {
  width: 100%;
  margin-top: 2px;
}
.hint {
  font-size: 11px;
  line-height: 1.4;
}
.hit-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.hit {
  border: 1px solid var(--border, #e3e8ef);
  border-radius: 5px;
  padding: 8px 10px;
  margin-bottom: 8px;
  cursor: pointer;
}
.hit.active {
  border-color: #2f9e8f;
  background: rgba(47, 158, 143, 0.06);
}
.hit-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.rank {
  font-family: monospace;
  color: #2f9e8f;
}
.hit-desc {
  margin: 6px 0;
  font-size: 13px;
  line-height: 1.5;
}
.hit-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.scores {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}
.scores th,
.scores td {
  text-align: left;
  padding: 2px 4px;
  border-bottom: 1px solid #eee;
}
.scores th {
  font-weight: normal;
  color: #556;
}
.chip {
  display: inline-block;
  border: 1px solid #cfd8e3;
  border-radius: 10px;
  padding: 0 6px;
  margin: 0 4px 4px 0;
  font-size: 11px;
}
.evidence {
  font-size: 12px;
  margin: 0;
}
.evidence dt {
  color: #667;
}
.evidence dd {
  margin: 0 0 6px;
  word-break: break-all;
}
.mono {
  font-family: monospace;
}
.small {
  font-size: 12px;
}
.debug-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}
.degraded {
  margin-top: 10px;
  border-left: 3px solid #d08b2c;
  padding-left: 10px;
  font-size: 13px;
}
.degraded ul {
  margin: 4px 0;
  padding-left: 18px;
}
</style>
