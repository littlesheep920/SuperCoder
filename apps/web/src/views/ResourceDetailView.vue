<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceDetail } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";
import VersionLifecyclePanel from "../components/VersionLifecyclePanel.vue";
import VersionEditor from "../components/VersionEditor.vue";
import { stateLabel } from "../domain/lifecycle";

const props = defineProps<{ id: string }>();
const detail = ref<ResourceDetail | null>(null);
const loading = ref(false);
const error = ref("");
const message = ref("");

/** Which version the lifecycle panel acts on. Defaults to the selected one. */
const activeVersionId = ref<string | null>(null);

// Offer the delete action only when the caller may attempt it.
const { can } = useCapabilities();

/**
 * The version the lifecycle panel operates on.
 *
 * Usually the selected version, but a resource with several versions needs to let
 * the user pick which one to advance — publishing an older version is legitimate.
 */
const activeVersion = computed(() => {
  if (!detail.value) return null;
  if (activeVersionId.value === null) return detail.value.selected_version;
  return (
    detail.value.versions.find((item) => item.version_id === activeVersionId.value) ??
    detail.value.selected_version
  );
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    detail.value = await api.getResource(props.id, true);
    // Keep the user's choice across a reload if that version still exists.
    if (
      activeVersionId.value !== null &&
      !detail.value.versions.some((item) => item.version_id === activeVersionId.value)
    ) {
      activeVersionId.value = null;
    }
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
          {{ stateLabel(detail.selected_version?.status ?? "draft") }}
        </span>
        <span class="muted">{{ detail.kind }} · {{ detail.owner_team }}</span>
        <button v-if="can('resource:delete')" class="danger" @click="remove">撤回/删除</button>
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
          <thead><tr><th>版本</th><th>状态</th><th>更新时间</th><th></th></tr></thead>
          <tbody>
            <tr v-for="v in detail.versions" :key="v.version_id">
              <td>
                <router-link :to="`/resources/${detail.resource_id}/versions`">{{ v.version }}</router-link>
              </td>
              <td><span class="badge" :class="v.status">{{ stateLabel(v.status) }}</span></td>
              <td>{{ new Date(v.status_changed_at).toLocaleString() }}</td>
              <td>
                <button
                  v-if="activeVersion?.version_id !== v.version_id"
                  class="link"
                  @click="activeVersionId = v.version_id"
                >
                  对此版本操作
                </button>
                <span v-else class="muted small">当前操作目标</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="muted small">
          生命周期面板作用于
          <strong>{{ activeVersion?.version ?? "—" }}</strong>
          （{{ stateLabel(activeVersion?.status ?? "") }}）。可在上表切换目标版本。
        </p>
      </div>

      <VersionLifecyclePanel :version="activeVersion" @changed="load" />

      <VersionEditor :version="activeVersion" @changed="load" />
    </template>
  </div>
</template>
