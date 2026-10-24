<script setup lang="ts">
import { ref } from "vue";
import { getToken, setToken } from "./api/client";

const token = ref(getToken() ?? "");
const saved = ref(!!getToken());

function save() {
  setToken(token.value.trim());
  saved.value = !!token.value.trim();
  window.location.reload();
}
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
      </div>
      <nav>
        <router-link to="/resources">能力资产</router-link>
        <router-link to="/register">资源登记</router-link>
        <router-link to="/jobs">任务监控</router-link>
      </nav>
    </aside>
    <main class="main">
      <router-view />
    </main>
  </div>
</template>
