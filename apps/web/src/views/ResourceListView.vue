<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceSummary } from "../api/types";

const items = ref<ResourceSummary[]>([]);
const loading = ref(false);
const error = ref("");
const kind = ref("");
const status = ref("");

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

onMounted(load);
</script>

<template>
  <div>
    <div class="row" style="margin-bottom: 12px">
      <h2 style="margin: 0">能力资产中心</h2>
      <select v-model="kind" @change="load" style="width: 160px">
        <option value="">全部类型</option>
        <option value="skill">skill</option>
        <option value="mcp">mcp</option>
        <option value="prompt">prompt</option>
        <option value="agent">agent</option>
        <option value="workflow">workflow</option>
        <option value="tool">tool</option>
      </select>
      <select v-model="status" @change="load" style="width: 160px">
        <option value="">全部状态</option>
        <option value="draft">draft</option>
        <option value="validating">validating</option>
        <option value="pending_review">pending_review</option>
        <option value="approved">approved</option>
        <option value="published">published</option>
        <option value="deprecated">deprecated</option>
        <option value="retired">retired</option>
      </select>
      <button class="primary" @click="load">刷新</button>
    </div>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <p v-else-if="items.length === 0" class="muted">暂无资源，请先在“资源登记”页登记。</p>

    <table v-else>
      <thead>
        <tr><th>名称</th><th>类型</th><th>版本</th><th>状态</th><th>团队</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.resource_id">
          <td>
            <router-link :to="`/resources/${item.resource_id}`">{{ item.slug }}</router-link>
          </td>
          <td>{{ item.kind }}</td>
          <td>{{ item.selected_version?.version ?? "—" }}</td>
          <td>
            <span class="badge" :class="item.selected_version?.status ?? 'draft'">
              {{ item.selected_version?.status ?? "draft" }}
            </span>
          </td>
          <td>{{ item.owner_team }}</td>
          <td>
            <router-link :to="`/resources/${item.resource_id}/versions`">版本</router-link> ·
            <router-link :to="`/resources/${item.resource_id}/reviews`">审核</router-link> ·
            <router-link :to="`/resources/${item.resource_id}/dependencies`">依赖</router-link>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
