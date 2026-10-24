<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceDetail } from "../api/types";

const props = defineProps<{ id: string }>();
const detail = ref<ResourceDetail | null>(null);
const loading = ref(false);
const error = ref("");
const message = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    detail.value = await api.getResource(props.id, true);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function remove() {
  const reason = window.prompt("撤回原因") ?? "";
  if (!reason) return;
  try {
    await api.deleteResource(props.id, reason);
    message.value = "已撤回（未发布版本全部转为 retired）";
    await load();
  } catch (e) {
    message.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
</script>

<template>
  <div>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <template v-else-if="detail">
      <div class="row" style="margin-bottom: 12px">
        <h2 style="margin: 0">{{ detail.slug }}</h2>
        <span class="badge" :class="detail.selected_version?.status ?? 'draft'">
          {{ detail.selected_version?.status ?? "draft" }}
        </span>
        <span class="muted">{{ detail.kind }} · {{ detail.owner_team }}</span>
        <button class="danger" @click="remove">撤回/删除</button>
      </div>
      <p class="muted">{{ message }}</p>

      <div class="card">
        <h3>概览</h3>
        <table>
          <tbody>
            <tr><th>名称</th><td>{{ detail.selected_version?.name }}</td></tr>
            <tr><th>版本</th><td>{{ detail.selected_version?.version }}</td></tr>
            <tr><th>制品哈希</th><td class="muted">{{ detail.selected_version?.artifact_hash }}</td></tr>
            <tr><th>入口</th><td>{{ detail.version?.entrypoint }}</td></tr>
            <tr><th>说明</th><td>{{ detail.version?.description }}</td></tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <h3>版本时间线</h3>
        <table>
          <thead><tr><th>版本</th><th>状态</th><th>更新时间</th></tr></thead>
          <tbody>
            <tr v-for="v in detail.versions" :key="v.version_id">
              <td>
                <router-link :to="`/resources/${detail.resource_id}/versions`">{{ v.version }}</router-link>
              </td>
              <td><span class="badge" :class="v.status">{{ v.status }}</span></td>
              <td>{{ new Date(v.status_changed_at).toLocaleString() }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
