<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceVersion } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const props = defineProps<{ id: string }>();
const versions = ref<ResourceVersion[]>([]);
const loading = ref(false);

const { can } = useCapabilities();
const error = ref("");
const baseId = ref("");
const targetId = ref("");
const diff = ref("");
const changedPaths = ref<string[]>([]);
const message = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const response = await api.listVersions(props.id);
    versions.value = response.items;
    if (versions.value.length >= 2) {
      baseId.value = versions.value[1].version_id;
      targetId.value = versions.value[0].version_id;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function runDiff() {
  try {
    const result = await api.diff(targetId.value, baseId.value);
    diff.value = result.unified_diff;
    changedPaths.value = result.changed_paths;
  } catch (e) {
    message.value = e instanceof Error ? e.message : String(e);
  }
}

async function rollback(versionId: string, version: string) {
  const reason = window.prompt(`回滚到 ${version} 的原因`) ?? "";
  if (!reason) return;
  try {
    await api.rollback(versionId, reason);
    message.value = `已从 ${version} 创建新的更高版本草稿`;
    await load();
  } catch (e) {
    message.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
</script>

<template>
  <div>
    <h2>版本与 Diff</h2>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <p class="muted">{{ message }}</p>

    <table v-if="versions.length">
      <thead><tr><th>版本</th><th>状态</th><th>制品哈希</th><th></th></tr></thead>
      <tbody>
        <tr v-for="v in versions" :key="v.version_id">
          <td>{{ v.version }}</td>
          <td><span class="badge" :class="v.status">{{ v.status }}</span></td>
          <td class="muted">{{ v.artifact_hash.slice(0, 24) }}…</td>
          <td>
          <button v-if="can('resource:edit')" @click="rollback(v.version_id, v.version)">回滚</button>
        </td>
        </tr>
      </tbody>
    </table>

    <div class="card" v-if="versions.length >= 2">
      <h3>Diff</h3>
      <div class="row">
        <select v-model="baseId" style="width: auto">
          <option v-for="v in versions" :key="v.version_id" :value="v.version_id">{{ v.version }}</option>
        </select>
        <span>→</span>
        <select v-model="targetId" style="width: auto">
          <option v-for="v in versions" :key="v.version_id" :value="v.version_id">{{ v.version }}</option>
        </select>
        <button class="primary" @click="runDiff">生成 Diff</button>
      </div>
      <p class="muted">变更字段：{{ changedPaths.join(", ") || "无" }}</p>
      <pre class="diff" v-if="diff">{{ diff }}</pre>
    </div>
  </div>
</template>
