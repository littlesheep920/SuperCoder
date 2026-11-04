<script setup lang="ts">
/**
 * Workflow runs and their reviews.
 *
 * Two things this screen is careful about:
 *
 * 1. A run's snapshot is what it pinned, not what the definition says now. Listing the pinned
 *    versions rather than re-reading the definition keeps the screen honest about what ran.
 * 2. A review shows what was recorded. A capability with no executions shows zero uses and
 *    says so; it is never shown as successful by default.
 */
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { WorkflowDefinition, WorkflowRunReview, WorkflowRunSummary } from "../api/types";

const definitions = ref<WorkflowDefinition[]>([]);
const runs = ref<WorkflowRunSummary[]>([]);
const selectedRun = ref<WorkflowRunSummary | null>(null);
const review = ref<WorkflowRunReview | null>(null);
const loading = ref(false);
const error = ref("");
const notice = ref("");

const chosenDefinition = ref("");
const runtime = ref("agent-skills");
const timeoutSeconds = ref(600);

/** A short, readable form for a digest; the full value stays in the title attribute. */
function shortHash(value: string | null | undefined): string {
  if (!value) return "—";
  const body = value.startsWith("sha256:") ? value.slice(7) : value;
  return body.length <= 16 ? body : `${body.slice(0, 8)}…${body.slice(-4)}`;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [definitionList, runList] = await Promise.all([
      api.listWorkflows(),
      api.listRuns({ limit: "50" }),
    ]);
    definitions.value = definitionList.items;
    runs.value = runList.items;
    if (!chosenDefinition.value && definitions.value.length) {
      chosenDefinition.value = definitions.value[0].workflow_id;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function startRun() {
  if (!chosenDefinition.value) {
    error.value = "请先选择一个工作流定义。";
    return;
  }
  error.value = "";
  notice.value = "";
  try {
    // The key makes a double click one run: the server returns the original, not a second.
    const key = `console-${chosenDefinition.value}-${Date.now()}`;
    const run = await api.createRun(
      chosenDefinition.value,
      { runtime: runtime.value, input: {}, timeout_seconds: timeoutSeconds.value },
      key,
    );
    notice.value = `已创建运行 ${run.run_id}，状态 ${run.status}。执行由 Worker 推进。`;
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function openRun(run: WorkflowRunSummary) {
  selectedRun.value = run;
  review.value = null;
  error.value = "";
  try {
    review.value = await api.getRunReview(run.run_id);
  } catch (e) {
    // A review needs usage records; a run that has not executed has an empty one, which the
    // server still returns. Any other failure is shown rather than hidden behind the run view.
    error.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
</script>

<template>
  <div>
    <div class="row" style="margin-bottom: var(--space-4)">
      <h2>工作流运行</h2>
      <span class="faint" v-if="!loading">{{ runs.length }} 次运行</span>
    </div>

    <p v-if="error" class="error">{{ error }} <button class="link" @click="load">重试</button></p>
    <p v-if="notice" class="ok small">{{ notice }}</p>

    <div class="card">
      <h3>发起运行</h3>
      <div v-if="definitions.length === 0" class="empty-state">
        <p class="title">还没有可运行的工作流</p>
        <p class="hint">
          工作流定义来自一个 `workflow` 类型的已发布资源。先登记并发布一个工作流定义。
        </p>
      </div>
      <template v-else>
        <div class="row">
          <select v-model="chosenDefinition" style="width: 320px" aria-label="工作流定义">
            <option v-for="definition in definitions" :key="definition.workflow_id" :value="definition.workflow_id">
              {{ definition.definition.title ?? definition.workflow_id }}
            </option>
          </select>
          <select v-model="runtime" style="width: 180px" aria-label="运行环境">
            <option value="agent-skills">agent-skills</option>
            <option value="claude-code">claude-code</option>
            <option value="dsh">dsh</option>
          </select>
          <button class="primary" @click="startRun">创建运行</button>
        </div>
        <p class="faint">
          运行创建时会把每个能力解析为精确版本并写入快照；解析失败则不会创建运行。
        </p>
      </template>
    </div>

    <p v-if="loading" class="muted">加载中…</p>

    <div v-else-if="runs.length === 0" class="card empty-state">
      <p class="title">还没有运行记录</p>
      <p class="hint">选择一个工作流定义创建运行后，这里会显示每次运行的状态与复盘。</p>
    </div>

    <table v-else>
      <thead>
        <tr>
          <th>运行</th>
          <th style="width: 150px">状态</th>
          <th style="width: 110px">发起人</th>
          <th style="width: 150px">快照哈希</th>
          <th style="width: 180px">创建时间</th>
          <th style="width: 100px"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs" :key="run.run_id">
          <td class="mono">{{ run.run_id }}</td>
          <td><span class="badge" :class="run.status">{{ run.status }}</span></td>
          <td class="muted">{{ run.requested_by }}</td>
          <td class="mono muted" :title="run.resolution_snapshot_hash">
            {{ shortHash(run.resolution_snapshot_hash) }}
          </td>
          <td class="faint">{{ new Date(run.created_at).toLocaleString() }}</td>
          <td><button class="link" @click="openRun(run)">复盘</button></td>
        </tr>
      </tbody>
    </table>

    <!-- The review, when one is open. -->
    <div v-if="selectedRun" class="card" style="margin-top: var(--space-4)">
      <div class="row" style="justify-content: space-between">
        <h3 style="margin: 0">复盘 {{ selectedRun.run_id }}</h3>
        <button class="link" @click="selectedRun = null; review = null">关闭</button>
      </div>

      <p v-if="!review" class="muted">加载复盘中…</p>
      <template v-else>
        <p class="faint">
          复盘摘要 <span class="mono">{{ shortHash(review.review_digest) }}</span>
          ：内容变化时该摘要随之变化，可用于确认读到的是记录本身。
        </p>

        <p v-for="note in review.notes" :key="note" class="warn small">{{ note }}</p>

        <table>
          <thead>
            <tr>
              <th>阶段</th>
              <th>门禁</th>
              <th>能力</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="stage in review.stages" :key="stage.stage_id">
              <td>{{ stage.title }}</td>
              <td>
                <span v-if="stage.gate === 'human-approval'" class="badge pending_review">人工审批</span>
                <span v-else class="faint">无</span>
              </td>
              <td class="faint">{{ (stage.capabilities ?? []).length }} 项</td>
            </tr>
          </tbody>
        </table>

        <table style="margin-top: var(--space-3)">
          <thead>
            <tr>
              <th>能力</th>
              <th style="width: 90px">版本</th>
              <th style="width: 70px">使用</th>
              <th style="width: 70px">成功</th>
              <th style="width: 70px">失败</th>
              <th>产出哈希</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in review.capabilities" :key="item.node_id">
              <td class="mono">{{ item.kind }}/{{ item.slug }}</td>
              <td class="mono">{{ item.version }}</td>
              <td>{{ item.uses }}</td>
              <td :class="item.succeeded > 0 ? 'ok' : 'faint'">{{ item.succeeded }}</td>
              <td :class="item.failed > 0 ? 'error' : 'faint'">{{ item.failed }}</td>
              <td class="mono muted">
                <span v-if="item.output_hashes.length === 0" class="faint">—</span>
                <span v-for="hash in item.output_hashes" :key="hash" :title="hash">
                  {{ shortHash(hash) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>

        <p class="faint">
          共 {{ review.usage_rows }} 条使用记录。
          <span v-if="review.approval.approved_by">
            审批人 <strong>{{ review.approval.approved_by }}</strong>
            <span v-if="review.approval.reason">：{{ review.approval.reason }}</span>
          </span>
          <span v-else>该运行没有经过审批门。</span>
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
button.link {
  background: none;
  border: none;
  color: var(--info);
  padding: 0;
  font-size: 12.5px;
  text-decoration: underline;
}
</style>
