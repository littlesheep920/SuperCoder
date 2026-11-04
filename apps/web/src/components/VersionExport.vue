<script setup lang="ts">
/**
 * Export a published version to a runtime environment.
 *
 * The design constraint: an export can lose capabilities. Typed inputs, other runtimes,
 * and a workflow's enforced ordering do not survive a trip into a plain skill file. So the
 * panel shows the losses *before* the download and repeats them after, rather than handing
 * over a zip that looks complete.
 *
 * Only published versions are offered. The server enforces that independently; the UI
 * mirrors it so the reason is visible instead of arriving as a refusal after a click.
 */
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ExportDownload, ExportTarget, ResourceVersionSummary } from "../api/types";

/**
 * The version fields this panel reads.
 *
 * Declared structurally rather than importing the full `ResourceVersion`: the detail page
 * supplies a summary, and requiring the larger type would force a widening that the
 * component does not need.
 */
type ExportableVersion = ResourceVersionSummary & {
  status: string;
  manifest?: { inputs?: unknown[]; outputs?: unknown[]; kind?: string };
  runtimes?: Array<string | { runtime: string }>;
};

const props = defineProps<{ version: ExportableVersion | null }>();

const targets = ref<ExportTarget[]>([]);
const selected = ref("");
const error = ref("");
const busy = ref(false);
const download = ref<ExportDownload | null>(null);

/** The server refuses non-published versions; say so before the click, not after. */
const publishable = computed(() => props.version?.status === "published");

/** Warnings predicted from the manifest, so the user sees the cost before downloading. */
const predicted = computed(() => {
  const version = props.version;
  if (!version) return [];
  const losses: Array<{ code: string; detail: string }> = [];

  const inputs = version.manifest?.inputs ?? [];
  const outputs = version.manifest?.outputs ?? [];
  if (inputs.length || outputs.length) {
    losses.push({
      code: "typed_io_not_carried",
      detail: `清单声明的 ${inputs.length} 个输入与 ${outputs.length} 个输出不会由目标环境校验，只作为正文描述。`,
    });
  }

  const runtimes = (version.runtimes ?? []).map((entry) =>
    typeof entry === "string" ? entry : entry.runtime,
  );
  const others = runtimes.filter((runtime) => runtime !== selected.value);
  if (selected.value && others.length) {
    losses.push({
      code: "other_runtimes_not_exported",
      detail: `该资源还声明了 ${others.join("、")}，本次只导出所选环境。`,
    });
  }

  if (version.manifest?.kind === "workflow") {
    losses.push({
      code: "workflow_degrades_to_instructions",
      detail: "工作流会导出为说明文字，步骤顺序与门禁不再由目标环境强制执行。",
    });
  }

  return losses;
});

async function loadTargets() {
  try {
    const response = await api.listExportTargets();
    targets.value = response.items;
    if (!selected.value && response.items.length) {
      selected.value = response.items[0].identifier;
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function run() {
  const version = props.version;
  if (!version || !selected.value || busy.value) return;
  busy.value = true;
  error.value = "";
  download.value = null;
  try {
    const result = await api.exportVersion(version.version_id, selected.value);
    download.value = result;
    api.saveDownload(result);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    busy.value = false;
  }
}

/**
 * Explain a warning code in Chinese.
 *
 * The server sends codes in a header; the wording lives here so the panel can explain a
 * code it did not predict. Unknown codes are shown verbatim rather than hidden — an
 * unexplained warning is still a warning.
 */
const WARNING_TEXT: Record<string, string> = {
  typed_io_not_carried: "清单声明的输入/输出不会被目标环境校验。",
  other_runtimes_not_exported: "资源声明的其他运行环境本次未导出。",
  workflow_degrades_to_instructions: "工作流导出为说明文字，步骤顺序与门禁不再强制执行。",
  entrypoint_renamed: "入口文件已改为 SKILL.md，该文件名是目标环境的加载入口。",
  description_truncated: "描述超长，标准上限为 1024 字符。",
  mcp_has_no_config_home: "该环境没有文件化的 MCP 配置，服务器定义未导出。",
  mcp_transport_unsupported: "该环境不支持此 MCP 传输方式，未导出。",
  upload_is_manual: "需在目标环境的手册页面上传，无法通过 API 自动安装。",
};

function describeWarning(code: string): string {
  return WARNING_TEXT[code] ?? "（未识别的有损项，请查看服务端说明）";
}

onMounted(loadTargets);
</script>

<template>
  <section class="card">
    <h3 style="margin-top: 0">导出到运行环境</h3>

    <p v-if="!version" class="muted">请先选择一个版本。</p>

    <template v-else>
      <p v-if="!publishable" class="muted small">
        只有<strong>已发布</strong>的版本可以导出；当前状态为
        <code>{{ version.status }}</code>。未发布内容尚未完成审核，导出通道不绕过该门禁。
      </p>

      <div class="row" v-if="publishable">
        <label class="muted small" for="export-target">目标环境</label>
        <select id="export-target" v-model="selected">
          <option v-for="target in targets" :key="target.identifier" :value="target.identifier">
            {{ target.display_name }}（{{ target.skills_directory }}）
          </option>
        </select>
        <button class="primary" :disabled="busy || !selected" @click="run">
          {{ busy ? "导出中…" : "导出并下载" }}
        </button>
      </div>

      <div v-if="publishable" class="warn-block" :class="{ 'no-loss': !predicted.length }">
        <p class="warn-title">
          {{ predicted.length ? "本次导出会丢失以下能力：" : "本次导出没有已知的有损项。" }}
        </p>
        <ul v-if="predicted.length">
          <li v-for="loss in predicted" :key="loss.code">
            <code>{{ loss.code }}</code> — {{ loss.detail }}
          </li>
        </ul>
        <p v-else class="muted small">
          这不等于「毫无损」：目标环境是否真正加载该能力，取决于其版本与配置；平台只保证静态格式合规。
        </p>
      </div>

      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="download" class="ok-block">
        <p>
          已生成 <code>{{ download.filename }}</code>
          <span class="muted small">（内容摘要 {{ download.digest.slice(0, 23) }}…）</span>
        </p>
        <p v-if="download.warningCodes.length" class="warn-title">
          服务端确认的有损项：
        </p>
        <ul v-if="download.warningCodes.length">
          <li v-for="code in download.warningCodes" :key="code">
            <code>{{ code }}</code> — {{ describeWarning(code) }}
          </li>
        </ul>
        <p v-else class="muted small">服务端确认本次导出没有有损项。</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.warn-block {
  border-left: 3px solid #f59e0b;
  padding: 8px 12px;
  margin: 10px 0;
  background: rgba(245, 158, 11, 0.08);
}
.warn-block.no-loss {
  border-left-color: #4ade80;
  background: rgba(74, 222, 128, 0.08);
}
.warn-block ul {
  margin: 6px 0 0 18px;
  padding: 0;
}
.warn-title {
  margin: 0;
  font-weight: 600;
}
.ok-block {
  border-left: 3px solid #4ade80;
  padding: 8px 12px;
  margin: 10px 0;
  background: rgba(74, 222, 128, 0.08);
}
.ok-block ul {
  margin: 6px 0 0 18px;
  padding: 0;
}
</style>
