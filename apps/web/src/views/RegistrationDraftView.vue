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

import { computed, ref, watch } from "vue";
import { api } from "../api/client";
import type {
  DraftCommitResponse,
  DraftPreviewResponse,
  ManifestDraftResponse,
} from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const { can } = useCapabilities();

const description = ref("");
const kindHint = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
const draft = ref<ManifestDraftResponse | null>(null);

const preview = ref<DraftPreviewResponse | null>(null);
const previewLoading = ref(false);
/** Explicit human confirmation. The commit button stays disabled until checked. */
const confirmed = ref(false);
const commitReason = ref("");
const committing = ref(false);
const committed = ref<DraftCommitResponse | null>(null);
const commitError = ref<string | null>(null);

/** Drafting prepares a registration, so it needs the permission to create one. */
const allowed = computed(() => can("resource:create"));

const kinds = ["", "skill", "mcp", "prompt", "agent", "workflow", "tool"] as const;

/**
 * The reviewer edits the manifest directly.
 *
 * Without this the flow deadlocks: the model flags fields it could not infer
 * (``needs_input``), commit refuses while any remain, and a person has no way to
 * supply them. Editing is also what makes "what you approve is what gets written"
 * literal — the committed bytes are exactly what sits in this box.
 */
const manifestText = ref("");
const manifestParseError = ref<string | null>(null);

/** The manifest actually submitted: the edited text when it parses. */
const editedManifest = computed<Record<string, unknown> | null>(() => {
  if (!manifestText.value.trim()) return null;
  try {
    const parsed = JSON.parse(manifestText.value);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
});

watch(manifestText, (value) => {
  if (!value.trim()) {
    manifestParseError.value = null;
    return;
  }
  try {
    JSON.parse(value);
    manifestParseError.value = null;
  } catch (cause) {
    manifestParseError.value = `清单不是合法 JSON：${cause instanceof Error ? cause.message : ""}`;
  }
});

/**
 * Commit is reachable only when every precondition holds. Each of these is a
 * separate reason a person might not be allowed to proceed, so the button reports
 * the first unmet one instead of a generic "disabled".
 *
 * ``needs_input`` is no longer a blocker: the editor lets the reviewer supply the
 * missing values, and clearing them is what the field was asking for.
 */
const commitBlockers = computed<string[]>(() => {
  const blockers: string[] = [];
  if (!editedManifest.value) {
    blockers.push(manifestParseError.value ? "清单不是合法 JSON" : "还没有草稿");
  } else if (!previewValid.value) {
    blockers.push("草稿未通过协议校验");
  }
  if (preview.value?.base_present && !preview.value.identical) {
    blockers.push("这会修改一个已存在的资源");
  }
  if (!confirmed.value) blockers.push("尚未勾选确认");
  if (!commitReason.value.trim()) blockers.push("缺少登记原因");
  return blockers;
});

/** Validation of whatever is currently in the editor, refreshed by the watch. */
const previewValid = ref(true);

const canCommit = computed(() => allowed.value && commitBlockers.value.length === 0);

/**
 * Re-preview whenever the edited manifest changes. Preview writes nothing, so it
 * is safe to call on every edit and keeps the diff honest as the reviewer types.
 */
let previewTimer: ReturnType<typeof setTimeout> | null = null;
watch(
  editedManifest,
  (manifest) => {
    committed.value = null;
    confirmed.value = false;
    if (previewTimer) clearTimeout(previewTimer);
    if (!manifest) {
      preview.value = null;
      return;
    }
    // Debounce: a person typing JSON should not fire a request per keystroke.
    previewTimer = setTimeout(async () => {
      previewLoading.value = true;
      try {
        const result = await api.previewDraft({ manifest });
        preview.value = result;
        previewValid.value = result.validation_valid;
      } catch (cause) {
        preview.value = null;
        commitError.value = cause instanceof Error ? cause.message : "无法生成差异";
      } finally {
        previewLoading.value = false;
      }
    }, 400);
  },
  { immediate: true },
);

/** Seed the editor from each new draft. */
watch(
  () => draft.value?.manifest,
  (manifest) => {
    manifestText.value = manifest ? JSON.stringify(manifest, null, 2) : "";
  },
);

async function submit(): Promise<void> {
  if (!description.value.trim() || loading.value) return;
  loading.value = true;
  error.value = null;
  draft.value = null;
  commitError.value = null;
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

async function commit(): Promise<void> {
  const manifest = editedManifest.value;
  if (!manifest || !canCommit.value || committing.value) return;
  committing.value = true;
  commitError.value = null;
  try {
    // Send exactly what is in the editor: the reviewer approves these bytes.
    committed.value = await api.commitDraft({
      manifest,
      reason: commitReason.value.trim(),
      confirmed: true,
    });
  } catch (cause) {
    commitError.value = describeCommitFailure(cause);
  } finally {
    committing.value = false;
  }
}

/**
 * Explain a commit failure in terms the reviewer can act on.
 *
 * The server's `detail` is precise but written for an operator ("requires a
 * writable managed Git repository"). A person on this screen needs to know
 * whether to retry, ask an admin, or fix the draft, so the status code decides
 * the message while the raw detail is kept alongside it.
 */
function describeCommitFailure(cause: unknown): string {
  const status = (cause as { status?: number } | null)?.status;
  const detail = cause instanceof Error ? cause.message : "";
  const suffix = detail ? `（${detail}）` : "";
  if (status === 503) {
    return `提交未执行：服务端当前没有可写存储（MySQL 未启用或 Git 为只读），草稿没有被写入。${suffix}`;
  }
  if (status === 422) {
    return `提交被拒绝：清单未通过服务端校验。${suffix}`;
  }
  if (status === 403) {
    return "提交被拒绝：当前身份没有创建资源的权限。";
  }
  return detail || "提交失败";
}

function reset(): void {
  description.value = "";
  kindHint.value = "";
  draft.value = null;
  manifestText.value = "";
  manifestParseError.value = null;
  preview.value = null;
  committed.value = null;
  confirmed.value = false;
  commitReason.value = "";
  error.value = null;
  commitError.value = null;
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
        <p v-if="draft.needs_input.length" class="note">
          补充标注的字段后再提交。可直接编辑下面的内容，提交的是你修改后的版本。
        </p>
        <textarea
          v-model="manifestText"
          class="manifest-editor"
          spellcheck="false"
          rows="18"
          :disabled="committing"
        />
        <p v-if="manifestParseError" class="notice error">{{ manifestParseError }}</p>
      </div>

      <!-- Diff: what committing would actually change. -->
      <div v-if="draft.manifest" class="diff-block">
        <h3>提交后会有什么变化</h3>
        <p v-if="previewLoading" class="note">正在计算差异…</p>
        <template v-else-if="preview">
          <p v-if="!preview.base_present" class="note new-resource">
            这是<strong>新资源</strong>：当前没有同名的可见资源。
          </p>
          <p v-else-if="preview.identical" class="note">
            与现有资源 <code>{{ preview.existing_resource_id }}</code> 完全相同。
          </p>
          <p v-else class="note replace-warning">
            这会<strong>修改已存在的资源</strong>
            <code>{{ preview.existing_resource_id }}</code>
            （当前版本 v{{ preview.existing_version }}）。
          </p>

          <details v-if="preview.unified_diff" class="unified">
            <summary>统一差异</summary>
            <pre>{{ preview.unified_diff }}</pre>
          </details>

          <ul v-if="preview.changed_paths.length || preview.added_paths.length || preview.removed_paths.length" class="paths">
            <li v-for="path in preview.changed_paths" :key="`c-${path}`" class="changed">改 {{ path }}</li>
            <li v-for="path in preview.added_paths" :key="`a-${path}`" class="added">增 {{ path }}</li>
            <li v-for="path in preview.removed_paths" :key="`r-${path}`" class="removed">删 {{ path }}</li>
          </ul>
        </template>
        <p v-else class="note">无法获取差异。</p>
      </div>

      <!-- Commit: the only action that writes. -->
      <div v-if="draft.manifest" class="commit-block">
        <h3>确认并提交</h3>

        <div v-if="committed" class="notice created">
          已创建资源 <code>{{ committed.resource_id }}</code>（版本 v{{ committed.version }}，
          状态 <code>{{ committed.created_status }}</code>）。<br />
          {{ committed.next_step }}
        </div>

        <template v-else>
          <p class="note">
            提交会把草稿写入 Git 并登记为一个<strong>草稿版本</strong>。内容正文是占位符，需要人工补充后才可发布。
            这一步不会发布、不会审批。
          </p>

          <label for="reason">登记原因</label>
          <input
            id="reason"
            v-model="commitReason"
            type="text"
            placeholder="例如：团队内部 SQL 检查工具，已在代码评审中试用"
            :disabled="committing"
          />

          <label class="confirm">
            <input v-model="confirmed" type="checkbox" :disabled="committing" />
            我已核对上面的清单与差异，确认提交
          </label>

          <p v-if="commitBlockers.length" class="note blockers">
            还不能提交：{{ commitBlockers.join("；") }}
          </p>

          <button type="button" :disabled="!canCommit || committing" @click="commit">
            {{ committing ? "提交中…" : "确认并提交" }}
          </button>
        </template>

        <p v-if="commitError" class="notice error">{{ commitError }}</p>
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
.manifest-editor {
  width: 100%;
  margin: 0;
  padding: 0.8rem;
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #1e293b;
  border-radius: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.82rem;
  line-height: 1.5;
  resize: vertical;
  white-space: pre;
  overflow-x: auto;
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

/* --- diff and commit --- */
.diff-block h3,
.commit-block h3 {
  margin: 0 0 0.3rem;
  font-size: 1rem;
}
.diff-block,
.commit-block {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 0.7rem 0.9rem;
  background: #111827;
}
.note.new-resource {
  color: #166534;
}
.note.replace-warning {
  color: #9a3412;
  font-weight: 600;
}
.unified pre {
  margin: 0.4rem 0 0;
  padding: 0.6rem;
  background: #020617;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 0.78rem;
  line-height: 1.45;
  max-height: 18rem;
  white-space: pre-wrap;
  word-break: break-word;
}
.unified summary {
  cursor: pointer;
  color: #9ca3af;
  font-size: 0.85rem;
}
.paths {
  margin: 0.5rem 0 0;
  padding-left: 1.1rem;
  font-size: 0.82rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.paths .changed {
  color: #fbbf24;
}
.paths .added {
  color: #4ade80;
}
.paths .removed {
  color: #f87171;
}
.commit-block label {
  display: block;
  font-size: 0.9rem;
  font-weight: 600;
  margin: 0.5rem 0 0.2rem;
}
.commit-block input[type="text"] {
  width: 100%;
  padding: 0.5rem 0.6rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  font: inherit;
}
.commit-block label.confirm {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-weight: 400;
  margin: 0.7rem 0 0.4rem;
}
.note.blockers {
  color: #b45309;
}
.notice.created {
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  color: #075985;
}
</style>
