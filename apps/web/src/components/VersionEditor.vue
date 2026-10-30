<script setup lang="ts">
/**
 * Edit a draft version's body and manifest fields.
 *
 * Two constraints shape this screen:
 *
 * 1. **Only drafts are editable.** The server rejects edits to any other state, so
 *    the editor renders read-only outside draft rather than letting someone type a
 *    paragraph and then lose it to a 409.
 * 2. **The manifest is a JSON Merge Patch.** Sending the whole manifest would let a
 *    stale tab silently revert fields another person changed; a patch only touches
 *    the keys actually edited.
 *
 * The body is written to the managed checkout by the server, which then hashes what
 * it wrote. The client never computes a digest — it could only disagree.
 */

import { computed, ref, watch } from "vue";
import { api } from "../api/client";
import type { ResourceVersion, ResourceVersionSummary } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";
import { versionEtag } from "../domain/lifecycle";

// The parent usually holds a summary (version list); the editor fetches the full
// record itself so it can show the manifest fields the summary omits.
const props = defineProps<{ version: ResourceVersion | ResourceVersionSummary | null }>();
const emit = defineEmits<{ (event: "changed"): void }>();

const { can } = useCapabilities();

const loading = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const stale = ref(false);

const body = ref("");
const originalBody = ref("");
const entrypoint = ref("");
const bodyAvailable = ref(false);

/** Manifest keys that are safe to edit here; lifecycle fields are not. */
const EDITABLE_KEYS = ["version", "name", "description", "owner_team", "tags", "aliases"] as const;
const fields = ref<Record<string, string>>({});
const originalFields = ref<Record<string, string>>({});

const isDraft = computed(() => props.version?.status === "draft");
const mayEdit = computed(() => can("resource:edit"));
const editable = computed(() => isDraft.value && mayEdit.value);

const bodyChanged = computed(() => body.value !== originalBody.value);
const manifestChanged = computed(() =>
  EDITABLE_KEYS.some((key) => (fields.value[key] ?? "") !== (originalFields.value[key] ?? "")),
);

/**
 * A body edit requires a new manifest version.
 *
 * Saving a draft appends a new version row and keeps the old one as immutable
 * history, so (resource, version) must not repeat. Surfacing this here means the
 * user is told what to change instead of receiving a unique-constraint error.
 */
const versionBumped = computed(
  () => (fields.value.version ?? "") !== (originalFields.value.version ?? ""),
);
const needsVersionBump = computed(() => bodyChanged.value && !versionBumped.value);

/** Suggest the next patch version, so the common case is one click. */
const suggestedVersion = computed(() => {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(originalFields.value.version ?? "");
  if (match === null) return "";
  return `${match[1]}.${match[2]}.${Number(match[3]) + 1}`;
});

function applySuggestion(): void {
  if (suggestedVersion.value) fields.value.version = suggestedVersion.value;
}

const canSave = computed(
  () =>
    editable.value &&
    (bodyChanged.value || manifestChanged.value) &&
    !needsVersionBump.value,
);

/** Build the JSON Merge Patch from the keys that actually changed. */
function buildPatch(): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const key of EDITABLE_KEYS) {
    const before = originalFields.value[key] ?? "";
    const after = fields.value[key] ?? "";
    if (before === after) continue;
    // List-valued fields are edited as comma-separated text.
    if (key === "tags" || key === "aliases") {
      patch[key] = after
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    } else {
      patch[key] = after;
    }
  }
  return patch;
}

function asText(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  return value === null || value === undefined ? "" : String(value);
}

async function load(): Promise<void> {
  const version = props.version;
  if (!version) return;
  loading.value = true;
  error.value = "";
  notice.value = "";
  stale.value = false;
  try {
    const detail = await api.getVersion(version.version_id);
    entrypoint.value = detail.entrypoint;
    const next: Record<string, string> = {};
    for (const key of EDITABLE_KEYS) next[key] = asText(detail.manifest?.[key]);
    fields.value = next;
    originalFields.value = { ...next };

    const file = await api.readDraftBody(version.version_id);
    bodyAvailable.value = file.available;
    body.value = file.body ?? "";
    originalBody.value = file.body ?? "";
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  const version = props.version;
  if (!version || !canSave.value || saving.value) return;

  if (bodyChanged.value) {
    const confirmed = window.confirm(
      `将覆盖 ${entrypoint.value} 的正文（${originalBody.value.length} → ${body.value.length} 字符）。\n\n` +
        "文件会写入托管的 Git 工作树。确认保存？",
    );
    if (!confirmed) return;
  }

  saving.value = true;
  error.value = "";
  notice.value = "";
  stale.value = false;
  try {
    const result = await api.editDraftBody(
      version.version_id,
      {
        body: bodyChanged.value ? body.value : null,
        manifest: manifestChanged.value ? buildPatch() : null,
        row_version: version.row_version,
        reason: "界面编辑草稿",
      },
      versionEtag(version.version_id, version.row_version),
    );
    notice.value = result.body_written
      ? `已保存：${result.body_path} 已写入，内容哈希已更新。`
      : "已保存：仅更新清单字段。";
    await load();
    emit("changed");
  } catch (cause) {
    const text = cause instanceof Error ? cause.message : String(cause);
    error.value = text;
    if (/ETag|row_version|stale|changed since/i.test(text) || /412/.test(text)) {
      stale.value = true;
    }
  } finally {
    saving.value = false;
  }
}

function revert(): void {
  body.value = originalBody.value;
  fields.value = { ...originalFields.value };
  error.value = "";
  notice.value = "";
}

watch(() => props.version?.version_id, load, { immediate: true });
</script>

<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h3 style="margin: 0">编辑草稿</h3>
      <span class="muted small">{{ entrypoint || "—" }}</span>
    </div>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="!version" class="muted">没有可编辑的版本。</p>
    <template v-else>
      <p v-if="!isDraft" class="muted small">
        只有草稿状态可以编辑；当前为 <strong>{{ version.status }}</strong>。如需修改，请先退回草稿。
      </p>
      <p v-else-if="!mayEdit" class="muted small">
        当前身份没有编辑权限（需要 <code>resource:edit</code>）。
      </p>
      <p v-if="isDraft" class="muted small">
        正文来自工作树中的 <code>{{ entrypoint || "—" }}</code>，不是该版本的快照：
        同一资源的所有版本共用这一个文件，因此这里显示的是文件当前内容。
        保存会写入文件并创建一个新版本，旧版本的哈希仍被记录，但其正文需从 Git 历史取得。
      </p>

      <p v-if="!bodyAvailable" class="error small">
        工作树中找不到 <code>{{ entrypoint }}</code>，正文无法编辑。
      </p>

      <div class="field-grid">
        <label v-for="key in EDITABLE_KEYS" :key="key" class="field">
          <span class="muted small">{{ key }}</span>
          <input v-model="fields[key]" :disabled="!editable || saving" />
        </label>
      </div>

      <label class="field">
        <span class="muted small">正文（{{ entrypoint }}）</span>
        <textarea
          v-model="body"
          rows="14"
          spellcheck="false"
          :disabled="!editable || saving || !bodyAvailable"
        />
      </label>

      <p v-if="needsVersionBump" class="warn small">
        保存正文需要一个新的版本号：旧版本会作为不可变历史保留，
        因此 <code>version</code> 必须与 <code>{{ originalFields.version }}</code> 不同。
        <button class="link" @click="applySuggestion" v-if="suggestedVersion">
          使用 {{ suggestedVersion }}
        </button>
      </p>

      <div class="row" style="gap: 10px; margin-top: 8px">
        <button :disabled="!canSave || saving" @click="save">
          {{ saving ? "保存中…" : "保存为" + (fields.version || "新版本") }}
        </button>
        <button :disabled="!canSave || saving" @click="revert">放弃修改</button>
        <span class="muted small">
          {{ bodyChanged ? "正文已修改" : "正文未变" }} ·
          {{ manifestChanged ? "清单已修改" : "清单未变" }}
        </span>
      </div>

      <p v-if="notice" class="ok">{{ notice }}</p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="stale" class="error small">
        该版本已被其他人修改，请刷新后再试。
        <button class="link" @click="load">重新加载</button>
      </p>
    </template>
  </div>
</template>

<style scoped>
.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
  margin: 10px 0;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field input,
.field textarea {
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border, #334155);
  background: var(--surface, #0f172a);
  color: inherit;
  font: inherit;
}
.field textarea {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.5;
  resize: vertical;
}
.small {
  font-size: 12px;
}
.ok {
  color: var(--ok, #4ade80);
}
.warn {
  color: var(--warn, #fbbf24);
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
