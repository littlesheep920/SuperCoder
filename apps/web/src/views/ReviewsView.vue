<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { ResourceReview, ResourceVersion } from "../api/types";
import { useCapabilities } from "../composables/useCapabilities";

const props = defineProps<{ id: string }>();
const reviews = ref<ResourceReview[]>([]);
const version = ref<ResourceVersion | null>(null);

// Review is the action most likely to be offered to the wrong person.
const { can } = useCapabilities();
const loading = ref(false);
const error = ref("");
const message = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const versions = await api.listVersions(props.id);
    if (versions.items.length) {
      const latest = versions.items[0];
      version.value = await api.getVersion(latest.version_id);
      const response = await api.listReviews(latest.version_id);
      reviews.value = response.items;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function approve() {
  if (!version.value) return;
  const rationale = window.prompt("审核意见") ?? "approve";
  try {
    await api.createReview(version.value.version_id, {
      review_type: "governance",
      decision: "approve",
      reviewer_role: "reviewer",
      artifact_hash: version.value.artifact_hash,
      manifest_snapshot_hash: version.value.manifest_snapshot_hash,
      runtime_scope: version.value.runtimes.map((r) => r.runtime),
      validation_evidence_ref: "evidence/stage1",
      permission_summary: { declared: [] },
      dependency_summary: { resolved: true, count: version.value.dependencies.length },
      evaluation_refs: [],
      rationale,
    });
    await load();
  } catch (e) {
    message.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
</script>

<template>
  <div>
    <h2>审核队列</h2>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <p class="muted">{{ message }}</p>

    <div class="row" style="margin-bottom: 12px" v-if="version">
      <span class="muted">当前版本 {{ version.version }}（{{ version.status }}）</span>
      <button v-if="can('lifecycle:review')" class="primary" @click="approve">批准（approve）</button>
      <span v-else class="muted small">需要审核角色才能批准</span>
    </div>

    <table v-if="reviews.length">
      <thead><tr><th>审核者</th><th>类型</th><th>结论</th><th>意见</th><th>时间</th></tr></thead>
      <tbody>
        <tr v-for="r in reviews" :key="r.review_id">
          <td>{{ r.reviewer_id }}</td>
          <td>{{ r.review_type }}</td>
          <td><span class="badge" :class="r.decision === 'approve' ? 'published' : 'draft'">{{ r.decision }}</span></td>
          <td>{{ r.rationale }}</td>
          <td>{{ new Date(r.decided_at).toLocaleString() }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted">暂无审核记录。</p>
  </div>
</template>
