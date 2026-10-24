<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { Job, JobEvent } from "../api/types";

const jobs = ref<Job[]>([]);
const loading = ref(false);
const error = ref("");
const events = ref<JobEvent[]>([]);
const selectedJob = ref<Job | null>(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const response = await api.listJobs();
    jobs.value = response.items;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function showEvents(job: Job) {
  selectedJob.value = job;
  try {
    const response = await api.listJobEvents(job.job_id);
    events.value = response.items;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function cancel(job: Job) {
  const reason = window.prompt("取消原因") ?? "cancelled";
  try {
    await api.cancelJob(job.job_id, reason, job.row_version, `"${job.job_id}:${job.row_version}"`);
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
</script>

<template>
  <div>
    <h2>异步任务监控</h2>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>

    <table v-if="jobs.length">
      <thead><tr><th>任务</th><th>类型</th><th>状态</th><th>尝试</th><th>错误</th><th></th></tr></thead>
      <tbody>
        <tr v-for="job in jobs" :key="job.job_id">
          <td class="muted">{{ job.job_id.slice(0, 12) }}…</td>
          <td>{{ job.job_kind }}</td>
          <td><span class="badge" :class="job.status">{{ job.status }}</span></td>
          <td>{{ job.attempt }}/{{ job.max_attempts }}</td>
          <td class="error">{{ job.error_summary ?? "" }}</td>
          <td>
            <button @click="showEvents(job)">事件</button>
            <button @click="cancel(job)" v-if="!['succeeded','failed','cancelled','dead_letter'].includes(job.status)">取消</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted">暂无任务。</p>

    <div class="card" v-if="selectedJob">
      <h3>事件流 — {{ selectedJob.job_id.slice(0, 12) }}…</h3>
      <table>
        <thead><tr><th>#</th><th>事件</th><th>状态转换</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="event in events" :key="event.sequence_no">
            <td>{{ event.sequence_no }}</td>
            <td>{{ event.event_type }}</td>
            <td>{{ event.from_status ?? "—" }} → {{ event.to_status ?? "—" }}</td>
            <td>{{ new Date(event.occurred_at).toLocaleString() }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
