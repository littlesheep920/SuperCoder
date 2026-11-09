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
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import type { WorkflowDefinition, WorkflowRunReview, WorkflowRunSummary } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const { can, identity } = useCapabilities();

/** The runtimes the server accepts for a run (the frozen data model's list). */
const RUNTIMES = ["claude-code", "cursor", "kiro", "dify", "openai-compatible-agent-runtime"] as const;
const FINISHED = ["succeeded", "failed", "cancelled", "timed_out"];

const definitions = ref<WorkflowDefinition[]>([]);
const runs = ref<WorkflowRunSummary[]>([]);
const selectedRun = ref<WorkflowRunSummary | null>(null);
const review = ref<WorkflowRunReview | null>(null);
const loading = ref(false);
const error = ref("");
const notice = ref("");

const chosenDefinition = ref("");
const runtime = ref<string>("claude-code");
const decisionReason = ref("");
const deciding = ref(false);

/**
 * Whether to offer the gate buttons. The server decides; this only avoids a button it would
 * refuse. Approving your own run is refused there too, so the button says why instead of failing.
 */
const canDecide = computed(() => can("workflow:approve"));
const isOwnRun = computed(
  () => !!selectedRun.value && (identity.value?.subject_id === selectedRun.value.requested_by || canDecide.value),
);

function isFinished(status: string): boolean {
  return FINISHED.includes(status);
}
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
    // The list can be stale, and a decision must carry the run's current row version.
    selectedRun.value = await api.getRun(run.run_id);
    review.value = await api.getRunReview(run.run_id);
  } catch (e) {
    // A review needs usage records; a run that has not executed has an empty one, which the
    // server still returns. Any other failure is shown rather than hidden behind the run view.
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function decide(decision: "approve" | "reject") {
  const run = selectedRun.value;
  if (!run || !run.approval_gate) return;
  const reason = decisionReason.value.trim();
  if (!reason) {
    error.value = "请填写审批理由：它会和决定一起记入审计记录。";
    return;
  }
  deciding.value = true;
  error.value = "";
  notice.value = "";
  try {
    const updated = await api.decideRunApproval(run.run_id, {
      gate: run.approval_gate,
      decision,
      reason,
      row_version: run.row_version,
    });
    notice.value =
      decision === "approve"
        ? `已批准 ${run.approval_gate}，运行将由 Worker 从该阶段继续。`
        : `已驳回 ${run.approval_gate}，运行已结束。`;
    decisionReason.value = "";
    await load();
    await openRun(updated);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    deciding.value = false;
  }
}

async function cancel(run: WorkflowRunSummary) {
  error.value = "";
  notice.value = "";
  try {
    await api.cancelRun(run.run_id);
    notice.value = `已请求取消 ${run.run_id}。Worker 处理后状态会变为 cancelled。`;
    await load();
    if (selectedRun.value?.run_id === run.run_id) await openRun(run);
  } catch (e) {
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
      <button class="link" @click="load">刷新</button>
    </div>

    <p v-if="error" class="error">{{ error }} <button class="link" @click="load">重试</button></p>
    <p v-if="notice" class="ok small">{{ notice }}</p>

    <div class="card">
      <h3>发起运行</h3>
      <div v-if="definitions.length === 0" class="empty-state">
        <p class="title">还没有可运行的工作流</p>
        <p class="hint">
          发布一个 workflow 类型的资源后，平台会把它编译成可运行的定义。目前只支持从输入到输出的单链流程，
          不含 condition 节点；不能编译的原因记在审计日志的 workflow.compile 事件里。
        </p>
      </div>
      <template v-else>
        <div class="row">
          <select v-model="chosenDefinition" style="width: 320px" aria-label="工作流定义">
            <option v-for="definition in definitions" :key="definition.workflow_id" :value="definition.workflow_id">
              {{ definition.definition.title ?? definition.workflow_id }}
            </option>
          </select>
          <select v-model="runtime" style="width: 240px" aria-label="运行环境">
            <option v-for="name in RUNTIMES" :key="name" :value="name">{{ name }}</option>
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
          <th style="width: 130px"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs" :key="run.run_id">
          <td class="mono">{{ run.run_id }}</td>
          <td>
            <span class="badge" :class="run.status">{{ run.status }}</span>
            <span v-if="run.status === 'awaiting_approval' && run.approval_gate" class="faint small">
              {{ run.approval_gate }}
            </span>
          </td>
          <td class="muted">{{ run.requested_by }}</td>
          <td class="mono muted" :title="run.resolution_snapshot_hash">
            {{ shortHash(run.resolution_snapshot_hash) }}
          </td>
          <td class="faint">{{ new Date(run.created_at).toLocaleString() }}</td>
          <td>
            <button class="link" @click="openRun(run)">复盘</button>
            <button
              v-if="!isFinished(run.status) && (identity?.subject_id === run.requested_by || canDecide)"
              class="link"
              style="margin-left: var(--space-2)"
              @click="cancel(run)"
            >
              取消
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- The review, when one is open. -->
    <div v-if="selectedRun" class="card" style="margin-top: var(--space-4)">
      <div class="row" style="justify-content: space-between">
        <h3 style="margin: 0">复盘 {{ selectedRun.run_id }}</h3>
        <button class="link" @click="selectedRun = null; review = null">关闭</button>
      </div>

      <p class="small" style="margin-top: var(--space-2)">
        状态 <span class="badge" :class="selectedRun.status">{{ selectedRun.status }}</span>
      </p>
      <p v-if="selectedRun.error_summary" class="error small">
        {{ selectedRun.error_code }}：{{ selectedRun.error_summary }}
      </p>

      <!-- A run waiting at a gate continues only after a reviewer decides. -->
      <section
        v-if="selectedRun.status === 'awaiting_approval' && selectedRun.approval_gate"
        class="card"
        style="margin: var(--space-3) 0"
        aria-label="审批"
      >
        <h4 style="margin: 0 0 var(--space-2)">等待审批：{{ selectedRun.approval_gate }}</h4>
        <p class="faint small">批准后运行从这一阶段继续；驳回会结束运行。决定和理由都会记入审计记录。</p>
        <template v-if="canDecide">
          <textarea
            v-model="decisionReason"
            rows="2"
            style="width: 100%"
            aria-label="审批理由"
            placeholder="写下你核对了什么，例如：发布检查清单全部通过"
          ></textarea>
          <div class="row" style="margin-top: var(--space-2)">
            <button class="primary" :disabled="deciding || isOwnRun" @click="decide('approve')">
              批准并继续
            </button>
            <button :disabled="deciding" @click="decide('reject')">驳回并结束运行</button>
          </div>
          <p v-if="isOwnRun" class="warn small">这是你发起的运行：你可以驳回，但批准需要另一位审核者。</p>
        </template>
        <p v-else class="muted small">你的账号没有审批权限（workflow:approve），请联系审核者处理。</p>
      </section>

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
