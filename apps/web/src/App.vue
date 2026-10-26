<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { getToken, setToken } from "./api/client";
import { useCapabilities } from "./composables/useCapabilities";

const token = ref(getToken() ?? "");
const saved = ref(!!getToken());

const { identity, load, reset } = useCapabilities();

const roleLabel = computed(() => {
  const roles = identity.value?.governance_roles ?? [];
  return roles.length ? roles.join(" / ") : "只读";
});

function save() {
  setToken(token.value.trim());
  saved.value = !!token.value.trim();
  // Capabilities belong to the previous token. Clearing them before the reload
  // guarantees a stale set can never authorize the UI for the new caller.
  reset();
  window.location.reload();
}

onMounted(() => {
  if (getToken()) void load();
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <h1>SuperCoder</h1>
      <div class="card" style="padding: 10px">
        <label>Bearer Token（开发用）</label>
        <input v-model="token" placeholder="dev-owner-token" />
        <div class="row" style="margin-top: 6px">
          <button @click="save">保存</button>
          <span v-if="saved" class="muted">已保存</span>
        </div>
        <p v-if="identity" class="muted small" style="margin-top: 6px">
          {{ identity.display_name }} · {{ roleLabel }}
        </p>
      </div>
      <nav>
        <router-link to="/find">智能查找</router-link>
        <router-link to="/discovery">检索工作台</router-link>
        <router-link to="/resources">能力资产</router-link>
        <router-link to="/register">资源登记</router-link>
        <router-link to="/register/draft">登记草稿</router-link>
        <router-link to="/jobs">任务监控</router-link>
      </nav>
    </aside>
    <main class="main">
      <router-view />
    </main>
  </div>
</template>
