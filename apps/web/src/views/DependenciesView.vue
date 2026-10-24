<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceVersion } from "../api/types";

const props = defineProps<{ id: string }>();
const version = ref<ResourceVersion | null>(null);
const loading = ref(false);
const error = ref("");

onMounted(async () => {
  loading.value = true;
  try {
    const versions = await api.listVersions(props.id);
    if (versions.items.length) {
      version.value = await api.getVersion(versions.items[0].version_id);
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div>
    <h2>依赖与运行环境</h2>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>

    <template v-else-if="version">
      <div class="card">
        <h3>运行环境</h3>
        <table>
          <thead><tr><th>Runtime</th><th>验证状态</th></tr></thead>
          <tbody>
            <tr v-for="r in version.runtimes" :key="r.runtime">
              <td>{{ r.runtime }}</td>
              <td><span class="badge draft">{{ r.validation_status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <h3>依赖</h3>
        <table v-if="version.dependencies.length">
          <thead><tr><th>类型</th><th>Slug</th><th>约束</th><th>来源</th><th>可选</th><th>解析状态</th></tr></thead>
          <tbody>
            <tr v-for="d in version.dependencies" :key="`${d.kind}-${d.slug}`">
              <td>{{ d.kind }}</td>
              <td>{{ d.slug }}</td>
              <td>{{ d.version }}</td>
              <td>{{ d.source }}</td>
              <td>{{ d.optional ? "是" : "否" }}</td>
              <td><span class="badge draft">{{ d.resolution_status }}</span></td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">该资源无依赖。</p>
      </div>
    </template>
  </div>
</template>
