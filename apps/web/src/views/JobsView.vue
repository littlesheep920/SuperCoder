<script setup lang="ts">
/**
 * Job monitoring: list, inspect events, cancel, and retry.
 *
 * Two behaviours worth stating, because both are easy to get subtly wrong:
 *
 * 1. **Retry does not mean done.** The endpoint answers 202: the job is requeued,
 *    not fixed. The UI refreshes the row and leaves the status as whatever the
 *    server says, so "retried" never renders as "succeeded".
 * 2. **The live stream is an enhancement, not the source of truth.** `EventSource`
 *    cannot send an Authorization header, so a stream may be refused by a proxy in
 *    front of the API. When that happens the page keeps the polled event list and
 *    says the stream is unavailable, rather than showing an empty timeline.
 */

import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "../api/client";
import type { Job, JobEvent } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const jobs = ref<Job[]>([]);
const loading = ref(false);
const error = ref("");
const events = ref<JobEvent[]>([]);
const selectedJob = ref<Job | null>(null);

const { can } = useCapabilities();

/** Live-stream state for the selected job. */
const streaming = ref(false);
const streamUnavailable = ref(false);
const liveEvents = ref<JobEvent[]>([]);
let source: EventSource | null = null;

/** Terminal states: a job here is not going to move on its own. */
const TERMINAL = ["succeeded", "failed", "cancelled", "dead_letter"];

/** A failed or dead-lettered job is the only thing retry makes sense for. */
function canRetry(job: Job): boolean {
  return can("job:retry") && ["failed", "dead_letter"].includes(job.status);
}

function canCancel(job: Job): boolean {
  return can("job:cancel") && !TERMINAL.includes(job.status);
}

/** Polled history merged with anything received live, ordered by sequence. */
const mergedEvents = computed<JobEvent[]>(() => {
  const bySequence = new Map<number, JobEvent>();
  for (const event of events.value) bySequence.set(event.sequence_no, event);
  for (const event of liveEvents.value) bySequence.set(event.sequence_no, event);
  return [...bySequence.values()].sort((a, b) => a.sequence_no - b.sequence_no);
});

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

function stopStream(): void {
  if (source !== null) {
    source.close();
    source = null;
  }
  streaming.value = false;
}

async function showEvents(job: Job) {
  selectedJob.value = job;
  liveEvents.value = [];
  stopStream();
  streamUnavailable.value = false;
  try {
    const response = await api.listJobEvents(job.job_id);
    events.value = response.items;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

/**
 * Follow the job's events live.
 *
 * `Last-Event-ID` is not sent explicitly: `EventSource` records the last id it saw
 * and replays it automatically on reconnect, which is how a dropped connection
 * resumes without duplicating or skipping events.
 */
function followLive(job: Job) {
  if (typeof EventSource === "undefined") {
    streamUnavailable.value = true;
    return;
  }
  stopStream();
  streamUnavailable.value = false;
  try {
    source = new EventSource(api.jobEventsStreamPath(job.job_id));
  } catch {
    streamUnavailable.value = true;
    return;
  }
  streaming.value = true;

  source.onmessage = (message: MessageEvent<string>) => {
    try {
      const parsed = JSON.parse(message.data) as JobEvent;
      liveEvents.value = [...liveEvents.value, parsed];
    } catch {
      // A heartbeat or a non-JSON frame is not an event; ignoring it is correct.
    }
  };

  // A named event carries the event type; register a generic listener for them.
  source.addEventListener("leased", (event) => capture(event));
  source.addEventListener("running", (event) => capture(event));
  source.addEventListener("succeeded", (event) => capture(event));
  source.addEventListener("failed", (event) => capture(event));
  source.addEventListener("cancelled", (event) => capture(event));
  source.addEventListener("dead_letter", (event) => capture(event));
  source.addEventListener("retry_scheduled", (event) => capture(event));

  source.onerror = () => {
    // The browser retries by itself; surface the state instead of hiding it.
    streaming.value = false;
    streamUnavailable.value = true;
  };
}

function capture(event: Event): void {
  const message = event as MessageEvent<string>;
  try {
    const parsed = JSON.parse(message.data) as JobEvent;
    liveEvents.value = [...liveEvents.value, parsed];
  } catch {
    // ignore malformed frames
  }
}

async function retry(job: Job) {
  const confirmed = window.confirm(
    `重新排队任务 ${job.job_id.slice(0, 12)}…？\n\n` +
      `当前状态 ${job.status}，已尝试 ${job.attempt}/${job.max_attempts} 次。\n` +
      "重新排队只表示任务被接受，不表示已经成功。",
  );
  if (!confirmed) return;
  try {
    await api.retryJob(job.job_id);
    // Report what the server says, not what we wish it said.
    await load();
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
onUnmounted(stopStream);
</script>

<template>
  <div>
    <h2>异步任务监控</h2>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>

    <table v-if="jobs.length">
      <thead>
        <tr><th>任务</th><th>类型</th><th>状态</th><th>尝试</th><th>错误</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="job in jobs" :key="job.job_id">
          <td class="muted mono">{{ job.job_id.slice(0, 12) }}…</td>
          <td>{{ job.job_kind }}</td>
          <td><span class="badge" :class="job.status">{{ job.status }}</span></td>
          <td>{{ job.attempt }}/{{ job.max_attempts }}</td>
          <td class="error small">{{ job.error_summary ?? "" }}</td>
          <td class="actions">
            <button @click="showEvents(job)">事件</button>
            <button v-if="canRetry(job)" @click="retry(job)">重试</button>
            <button v-if="canCancel(job)" @click="cancel(job)">取消</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted">暂无任务。</p>

    <div class="card" v-if="selectedJob">
      <div class="row" style="justify-content: space-between">
        <h3 style="margin: 0">事件流 — {{ selectedJob.job_id.slice(0, 12) }}…</h3>
        <div class="row" style="gap: 8px">
          <span class="muted small">{{ mergedEvents.length }} 条</span>
          <button v-if="!streaming" @click="followLive(selectedJob)">实时跟随</button>
          <button v-else @click="stopStream">停止跟随</button>
        </div>
      </div>

      <p v-if="streaming" class="ok small">正在实时跟随事件；断线后浏览器会自动带 Last-Event-ID 重连。</p>
      <p v-if="streamUnavailable" class="warn small">
        实时流不可用（浏览器无法为 EventSource 附加认证头，代理可能拒绝）。下方为已拉取的事件，可手动刷新。
      </p>

      <table>
        <thead><tr><th>#</th><th>事件</th><th>状态转换</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="event in mergedEvents" :key="event.sequence_no">
            <td>{{ event.sequence_no }}</td>
            <td class="mono">{{ event.event_type }}</td>
            <td>{{ event.from_status ?? "—" }} → {{ event.to_status ?? "—" }}</td>
            <td>{{ new Date(event.occurred_at).toLocaleString() }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="mergedEvents.length === 0" class="muted small">该任务还没有事件。</p>
    </div>
  </div>
</template>

<style scoped>
.actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.small {
  font-size: 12px;
}
.ok {
  color: var(--ok, #4ade80);
}
.warn {
  color: var(--warn, #fbbf24);
}
</style>
