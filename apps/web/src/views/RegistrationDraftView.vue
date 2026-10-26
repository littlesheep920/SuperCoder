<script setup lang="ts">
/**
 * Registration drafting: description in, reviewable manifest candidate out.
 *
 * The screen exists to make one thing unambiguous: **nothing is registered here.**
 * The server writes nothing, and this view must not let a reader believe otherwise.
 * So it always shows the next step, marks the schema check as a check rather than a
 * save, and offers no button that creates anything.
 *
 * A draft that fails validation is still shown. Hiding it would remove the exact
 * information a person needs to fix it — and the server deliberately returns an
 * invalid draft with errors rather than refusing to show it.
 */

import { computed, ref } from "vue";
import { api } from "../api/client";
import type { ManifestDraftResponse } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const { can } = useCapabilities();

const description = ref("");
const kindHint = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
const draft = ref<ManifestDraftResponse | null>(null);

/** Drafting prepares a registration, so it needs the permission to create one. */
const allowed = computed(() => can("resource:create"));

const kinds = ["", "skill", "mcp", "prompt", "agent", "workflow", "tool"] as const;

const prettyManifest = computed(() =>
  draft.value?.manifest ? JSON.stringify(draft.value.manifest, null, 2) : "",
);

async function submit(): Promise<void> {
  if (!description.value.trim() || loading.value) return;
  loading.value = true;
  error.value = null;
  draft.value = null;
  try {
    draft.value = await api.draftManifest({
      description: description.value.trim(),
      kind_hint: kindHint.value,
    });
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "生成草稿失败";
  } finally {
    loading.value = false;
  }
}

function reset(): void {
  description.value = "";
  kindHint.value = "";
  draft.value = null;
  error.value = null;
}
</script>

<template>
  <section class="draft">
    <header>
      <h2>登记草稿</h2>
      <p class="hint">
        用自然语言描述你想登记的能力，系统生成一份符合资源协议的清单草稿。
        <strong>这一步不会创建任何资源</strong>，需要人工确认后另行提交。
      </p>
    </header>

    <div v-if="!allowed" class="notice readonly">
      当前身份没有创建资源的权限，无法生成登记草稿。界面按只读渲染，服务端仍会拒绝越权请求。
    </div>

    <form class="ask" @submit.prevent="submit">
      <label for="description">能力描述</label>
      <textarea
        id="description"
        v-model="description"
        rows="4"
        placeholder="例如：我们做了个内部小工具，检查提交的 SQL 里有没有缺 WHERE 条件的全表更新，输入 SQL 文本，输出问题列表和行号，希望能在 Claude Code 里用。"
        :disabled="!allowed || loading"
      />
      <label for="kind">资源类型（可选，留空由系统判断）</label>
      <select id="kind" v-model="kindHint" :disabled="!allowed || loading">
        <option v-for="kind in kinds" :key="kind" :value="kind">
          {{ kind === "" ? "由系统判断" : kind }}
        </option>
      </select>
      <div class="actions">
        <button type="submit" :disabled="!allowed || loading || !description.trim()">
          {{ loading ? "生成中…" : "生成草稿" }}
        </button>
        <button type="button" class="ghost" :disabled="loading" @click="reset">清空</button>
      </div>
    </form>

    <p v-if="error" class="notice error">{{ error }}</p>

    <div v-if="draft" class="result">
      <p class="not-created">
        {{ draft.next_step }}
      </p>

      <p v-if="draft.degraded.length" class="notice degraded">
        降级运行：{{ draft.degraded.join("、") }}
      </p>

      <!-- A draft that would fail CI is shown with its exact errors. -->
      <div v-if="!draft.validation.valid" class="validation invalid">
        <h3>草稿未通过协议校验</h3>
        <ul>
          <li v-for="item in draft.validation.errors" :key="item"><code>{{ item }}</code></li>
        </ul>
        <p class="note">请修正后再提交。这份草稿现在提交会被服务端拒绝。</p>
      </div>
      <div v-else class="validation valid">
        <h3>草稿通过协议校验</h3>
        <p class="note">校验通过不代表已登记，仍需人工确认字段内容。</p>
      </div>

      <div v-if="draft.needs_input.length" class="needs">
        <h3>需要你补充</h3>
        <ul>
          <li v-for="item in draft.needs_input" :key="item"><code>{{ item }}</code></li>
        </ul>
      </div>

      <details v-if="draft.warnings.length" class="warnings">
        <summary>提示（{{ draft.warnings.length }} 条）</summary>
        <ul>
          <li v-for="item in draft.warnings" :key="item">{{ item }}</li>
        </ul>
      </details>

      <div v-if="draft.manifest" class="manifest">
        <h3>清单草稿</h3>
        <pre>{{ prettyManifest }}</pre>
      </div>

      <p v-if="draft.model" class="meta">生成模型：{{ draft.model }}</p>
    </div>
  </section>
</template>

<style scoped>
.draft {
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
.ask textarea,
.ask select {
  width: 100%;
  padding: 0.6rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  font: inherit;
}
.ask textarea {
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
.result {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.not-created {
  margin: 0;
  padding: 0.6rem 0.8rem;
  border-radius: 6px;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  color: #075985;
  font-weight: 600;
}
.validation h3,
.needs h3,
.manifest h3 {
  margin: 0 0 0.3rem;
  font-size: 1rem;
}
/* This console is dark-themed; a heading inheriting the page colour would vanish
   against the light status panels. Pin an explicit colour per panel. */
.validation.invalid {
  border: 1px solid #fecaca;
  background: #fef2f2;
  border-radius: 8px;
  padding: 0.7rem 0.9rem;
  color: #7f1d1d;
}
.validation.valid {
  border: 1px solid #bbf7d0;
  background: #f0fdf4;
  border-radius: 8px;
  padding: 0.7rem 0.9rem;
  color: #14532d;
}
.validation.invalid h3,
.validation.valid h3 {
  color: inherit;
}
.validation ul,
.needs ul,
.warnings ul {
  margin: 0.3rem 0;
  padding-left: 1.2rem;
}
.note {
  margin: 0.3rem 0 0;
  font-size: 0.85rem;
  color: #374151;
}
.manifest pre {
  margin: 0;
  padding: 0.8rem;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 0.82rem;
  line-height: 1.5;
}
.warnings summary {
  cursor: pointer;
  color: #4b5563;
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
