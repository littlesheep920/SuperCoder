<script setup lang="ts">
/**
 * Capability discovery: ask a question, get either grounded recommendations or an
 * auditable refusal.
 *
 * Two things this screen must never do:
 *
 * 1. **Present a refusal as a failure.** `answered: false` is a real answer. The
 *    screen shows the reason and the candidate list that was considered, because a
 *    refusal the user cannot inspect is indistinguishable from a bug.
 * 2. **Hide degradation.** When the model is unavailable the server answers from a
 *    heuristic that only fires on a clear score gap. That is reported, not hidden,
 *    so a reader can tell a judged answer from a heuristic one.
 */

import { computed, ref } from "vue";
import { api } from "../api/client";
import type { DiscoveryResponse } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const { can } = useCapabilities();

const question = ref("");
const context = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
const result = ref<DiscoveryResponse | null>(null);

/** Discovery reads the corpus; it never mutates, so `search:use` is the gate. */
const allowed = computed(() => can("search:use"));

async function submit(): Promise<void> {
  if (!question.value.trim() || loading.value) return;
  loading.value = true;
  error.value = null;
  result.value = null;
  try {
    result.value = await api.recommend({
      question: question.value.trim(),
      context: context.value.trim(),
    });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "请求失败";
  } finally {
    loading.value = false;
  }
}

function reset(): void {
  question.value = "";
  context.value = "";
  result.value = null;
  error.value = null;
}
</script>

<template>
  <section class="discovery">
    <header>
      <h2>能力发现</h2>
      <p class="hint">
        用自然语言描述你的需求，系统会在你有权访问的能力范围内给出推荐，并在没有合适能力时明确拒绝。
      </p>
    </header>

    <div v-if="!allowed" class="notice readonly">
      当前身份没有检索权限，无法使用能力发现。界面按只读渲染，服务端仍会拒绝越权请求。
    </div>

    <form class="ask" @submit.prevent="submit">
      <label for="question">你想要什么能力？</label>
      <textarea
        id="question"
        v-model="question"
        rows="3"
        placeholder="例如：帮我找一个能审查代码改动的能力，要给出每个问题的证据"
        :disabled="!allowed || loading"
      />
      <label for="context">补充信息（可选）</label>
      <textarea
        id="context"
        v-model="context"
        rows="2"
        placeholder="例如：我们用的是 TypeScript，只在合并前跑"
        :disabled="!allowed || loading"
      />
      <div class="actions">
        <button type="submit" :disabled="!allowed || loading || !question.trim()">
          {{ loading ? "检索中…" : "查找能力" }}
        </button>
        <button type="button" class="ghost" :disabled="loading" @click="reset">清空</button>
      </div>
    </form>

    <p v-if="error" class="notice error">{{ error }}</p>

    <!-- Refusal: a real answer, shown with its evidence. -->
    <div v-if="result && !result.answered" class="refusal">
      <h3>没有找到合适的能力</h3>
      <p class="reason">{{ result.refusal_reason ?? "没有可用的候选能力" }}</p>

      <div v-if="result.clarifying_questions.length" class="questions">
        <p>补充这些信息可能有助于判断：</p>
        <ul>
          <li v-for="item in result.clarifying_questions" :key="item">{{ item }}</li>
        </ul>
      </div>

      <details v-if="result.considered.length" class="considered">
        <summary>已检查的候选（{{ result.considered.length }} 个）</summary>
        <ul>
          <li v-for="item in result.considered" :key="item"><code>{{ item }}</code></li>
        </ul>
      </details>
    </div>

    <!-- Answer: every claim carries the version and hash it came from. -->
    <div v-else-if="result && result.answered" class="answer">
      <h3>推荐（{{ result.recommendations.length }} 个）</h3>
      <ol class="recommendations">
        <li v-for="item in result.recommendations" :key="item.resource_id">
          <div class="title">
            <RouterLink :to="`/resources/${encodeURIComponent(item.resource_id)}`">
              {{ item.resource_id }}
            </RouterLink>
            <span class="kind">{{ item.kind }}</span>
            <span class="version">v{{ item.version }}</span>
          </div>
          <p class="why">{{ item.reason }}</p>
          <p class="evidence">
            依据内容哈希 <code>{{ item.artifact_hash.slice(0, 16) }}…</code>
          </p>
        </li>
      </ol>
    </div>

    <p v-if="result?.degraded.length" class="notice degraded">
      降级运行：{{ result.degraded.join("、") }}。当前结果来自启发式判断，不是模型的完整评估。
    </p>
    <p v-if="result?.model" class="meta">判断模型：{{ result.model }}</p>
  </section>
</template>

<style scoped>
.discovery {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 60rem;
}
.hint {
  color: #555;
  margin: 0.25rem 0 0;
}
.ask {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.ask label {
  font-weight: 600;
  font-size: 0.9rem;
}
.ask textarea {
  width: 100%;
  padding: 0.6rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  font: inherit;
  resize: vertical;
}
.actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.4rem;
}
button {
  padding: 0.5rem 1rem;
  border-radius: 6px;
  border: 1px solid #2563eb;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
  font: inherit;
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
button.ghost {
  background: transparent;
  color: #2563eb;
}
.notice {
  padding: 0.6rem 0.8rem;
  border-radius: 6px;
  margin: 0;
}
.notice.error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
}
.notice.readonly {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  color: #4b5563;
}
.notice.degraded {
  background: #fffbeb;
  border: 1px solid #fde68a;
  color: #92400e;
}
.refusal {
  border: 1px solid #fde68a;
  background: #fffbeb;
  border-radius: 8px;
  padding: 0.8rem 1rem;
  /* The console is dark-themed; a heading inheriting the page colour would
     disappear against this light panel. */
  color: #78350f;
}
.refusal h3 {
  margin: 0 0 0.4rem;
  font-size: 1rem;
  color: inherit;
}
.reason {
  margin: 0 0 0.6rem;
  color: #78350f;
}
.questions ul,
.considered ul {
  margin: 0.3rem 0 0;
  padding-left: 1.2rem;
}
.considered {
  margin-top: 0.6rem;
}
.considered summary {
  cursor: pointer;
  color: #4b5563;
}
.answer h3 {
  margin: 0 0 0.5rem;
  font-size: 1rem;
}
.recommendations {
  margin: 0;
  padding-left: 1.2rem;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.recommendations li {
  padding: 0.6rem 0.8rem;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}
.title {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex-wrap: wrap;
}
.title a {
  font-weight: 600;
  color: #1d4ed8;
  text-decoration: none;
}
.kind,
.version {
  font-size: 0.78rem;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  background: #eef2ff;
  color: #3730a3;
}
.why {
  margin: 0.4rem 0 0.2rem;
}
.evidence {
  margin: 0;
  font-size: 0.8rem;
  color: #6b7280;
}
.meta {
  margin: 0;
  font-size: 0.8rem;
  color: #6b7280;
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.85em;
}
</style>
