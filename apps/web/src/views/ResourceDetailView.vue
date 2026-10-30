<script setup lang="ts">
/**
 * Resource detail: overview, versions, lifecycle, and editing in one place.
 *
 * The version selector is part of the URL (`?version=<id>`) rather than local
 * state, so a specific version can be linked, bookmarked, and survive a refresh.
 * That matters because "publish this older version" is a real action a reviewer
 * needs to share with someone else.
 */

import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api/client";
import type { ResourceDetail } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";
import VersionLifecyclePanel from "../components/VersionLifecyclePanel.vue";
import VersionEditor from "../components/VersionEditor.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { stateLabel } from "../domain/lifecycle";

const props = defineProps<{ id: string }>();
const route = useRoute();
const router = useRouter();

const detail = ref<ResourceDetail | null>(null);
const loading = ref(false);
const error = ref("");
const message = ref("");

const { can } = useCapabilities();

/** The version the panels act on; null means the resource's selected version. */
const activeVersionId = computed<string | null>(() => {
  const raw = route.query.version;
  return typeof raw === "string" && raw.length > 0 ? raw : null;
});

/** A link target that keeps the current resource and switches version. */
function linkFor(versionId: string | null) {
  const query = versionId === null ? {} : { version: versionId };
  return { path: `/resources/${props.id}`, query };
}

const activeVersion = computed(() => {
  if (!detail.value) return null;
  if (activeVersionId.value === null) return detail.value.selected_version;
  return (
    detail.value.versions.find((item) => item.version_id === activeVersionId.value) ??
    detail.value.selected_version
  );
});

/** True when the URL asks for a version that does not exist on this resource. */
const wrongVersionRequested = computed(
  () =>
    activeVersionId.value !== null &&
    detail.value !== null &&
    !detail.value.versions.some((item) => item.version_id === activeVersionId.value),
);

/** Versions newest first, which is the order `list_for_resource` already returns. */
const versions = computed(() => detail.value?.versions ?? []);

const summary = computed(() => detail.value?.selected_version ?? null);

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
watch(() => props.id, load);
</script>

<template>
  <div>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <template v-else-if="detail">
      <div class="row page-head">
        <h2 style="margin: 0">{{ detail.slug }}</h2>
        <StatusBadge :status="summary?.status" />
        <span class="muted">{{ detail.kind }} · {{ detail.owner_team }}</span>
        <button v-if="can('resource:delete')" class="danger" @click="remove">撤回/删除</button>
      </div>
      <p v-if="message" class="muted">{{ message }}</p>
      <p v-if="wrongVersionRequested" class="warn small">
        链接中的版本不在该资源下，已回退到最新版本。
        <router-link :to="linkFor(null)">清除</router-link>
      </p>

      <div class="card">
        <h3>概览</h3>
        <table>
          <tbody>
            <tr><th>名称</th><td>{{ summary?.name }}</td></tr>
            <tr><th>最新版本</th><td>{{ summary?.version ?? "—" }}</td></tr>
            <tr><th>状态</th><td><StatusBadge :status="summary?.status" small /></td></tr>
            <tr><th>制品哈希</th><td class="muted mono">{{ summary?.artifact_hash }}</td></tr>
            <tr><th>入口</th><td class="mono">{{ detail.version?.entrypoint }}</td></tr>
            <tr><th>说明</th><td>{{ detail.version?.description }}</td></tr>
            <tr><th>创建者</th><td>{{ detail.created_by }}</td></tr>
            <tr><th>版本数</th><td>{{ versions.length }}</td></tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <h3>版本</h3>
        <table>
          <thead>
            <tr><th>版本</th><th>状态</th><th>更新时间</th><th></th></tr>
          </thead>
          <tbody>
            <tr
              v-for="v in versions"
              :key="v.version_id"
              :class="{ selected: activeVersion?.version_id === v.version_id }"
            >
              <td>
                <router-link :to="linkFor(v.version_id)">{{ v.version }}</router-link>
                <span v-if="summary?.version_id === v.version_id" class="muted small"> · 最新</span>
              </td>
              <td><StatusBadge :status="v.status" small /></td>
              <td>{{ new Date(v.status_changed_at).toLocaleString() }}</td>
              <td>
                <span v-if="activeVersion?.version_id === v.version_id" class="muted small">
                  当前操作目标
                </span>
                <router-link v-else :to="linkFor(v.version_id)" class="link">切换到此版本</router-link>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="versions.length === 0" class="muted small">该资源没有可见版本。</p>
        <p class="muted small">
          生命周期与编辑面板作用于
          <strong>{{ activeVersion?.version ?? "—" }}</strong>
          （{{ stateLabel(activeVersion?.status ?? "") }}）。
          <router-link v-if="activeVersionId !== null" :to="linkFor(null)">回到最新版本</router-link>
        </p>
      </div>

      <VersionLifecyclePanel :version="activeVersion" @changed="load" />

      <VersionEditor :version="activeVersion" @changed="load" />

      <div class="card">
        <h3>相关</h3>
        <p class="small">
          <router-link :to="`/resources/${detail.resource_id}/versions`">版本与差异</router-link>
          ·
          <router-link :to="`/resources/${detail.resource_id}/reviews`">审核记录</router-link>
          ·
          <router-link :to="`/resources/${detail.resource_id}/dependencies`">依赖</router-link>
        </p>
        <p class="muted small">
          审核与依赖视图目前只作用于最新版本；如需针对特定版本操作，请先用上方版本表切换。
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page-head {
  margin-bottom: 12px;
  gap: 10px;
  flex-wrap: wrap;
}
.selected {
  background: rgba(56, 189, 248, 0.07);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.small {
  font-size: 12px;
}
.warn {
  color: var(--warn, #fbbf24);
}
.link {
  color: var(--accent, #38bdf8);
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
</style>
