<script setup lang="ts">
import { ref } from "vue";
import { api } from "../api/client";
import { useCapabilities } from "../composables/useCapabilities";

// Registering a resource is the one action that silently wastes the most typing
// when the caller lacks the role, so gate it before the form is even filled.
const { can, loaded } = useCapabilities();

const manifest = ref("");
const artifacts = ref("");
const reason = ref("手动登记");
const loading = ref(false);
const message = ref("");
const error = ref("");

async function submit() {
  loading.value = true;
  message.value = "";
  error.value = "";
  try {
    const manifestJson = JSON.parse(manifest.value);
    const artifactsJson = JSON.parse(artifacts.value || "[]");
    const created = await api.createResource({
      manifest: manifestJson,
      artifacts: artifactsJson,
      reason: reason.value,
    });
    message.value = `已登记资源 ${created.resource_id}（版本 ${created.version?.version}）`;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div>
    <h2>资源登记</h2>
    <p class="muted">
      登记已经存在于受管 Git 仓库中的资源（<code>resources/&lt;kind&gt;/&lt;slug&gt;/resource.yaml</code>）。
      系统会从 Git 读取正文并校验你提交的制品摘要。
    </p>
    <div class="card">
      <label>Manifest（JSON，与 resource.yaml 一致）</label>
      <textarea v-model="manifest" rows="18" placeholder='{"schema_version":"1","kind":"tool",...}'></textarea>
      <label>Artifacts（JSON 数组，含 path/digest/size_bytes）</label>
      <textarea v-model="artifacts" rows="4" placeholder='[{"path":"tool.py","digest":"sha256:...","size_bytes":123}]'></textarea>
      <label>原因</label>
      <input v-model="reason" />
      <div class="row" style="margin-top: 10px">
        <button
          v-if="can('resource:create')"
          class="primary"
          :disabled="loading"
          @click="submit"
        >
          提交登记
        </button>
        <span v-else-if="loaded" class="muted">
          你的账号没有登记权限，需要所有者（owner）角色。
        </span>
        <span v-else class="muted">正在确认身份…</span>
      </div>
      <p v-if="message" class="muted">{{ message }}</p>
      <p v-if="error" class="error">{{ error }}</p>
    </div>
  </div>
</template>
