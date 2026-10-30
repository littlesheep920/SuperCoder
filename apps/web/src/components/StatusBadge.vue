<script setup lang="ts">
/**
 * A lifecycle state badge.
 *
 * One component so the colours and labels cannot drift between the list, the
 * detail page, and the version timeline — the same state showing in two different
 * shades is a small thing that makes a console feel unreliable.
 */

import { computed } from "vue";
import { stateLabel } from "../domain/lifecycle";

const props = defineProps<{
  status: string | null | undefined;
  /** Compact variant for dense tables. */
  small?: boolean;
}>();

const label = computed(() => (props.status ? stateLabel(props.status) : "—"));
const slug = computed(() => props.status ?? "unknown");
</script>

<template>
  <span class="status-badge" :class="[slug, { small }]" :title="status ?? 'no status'">{{ label }}</span>
</template>

<style scoped>
.status-badge {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.6;
  border: 1px solid transparent;
  white-space: nowrap;
}
.status-badge.small {
  padding: 0 7px;
  font-size: 11px;
}
.draft {
  background: rgba(148, 163, 184, 0.16);
  border-color: rgba(148, 163, 184, 0.45);
  color: #cbd5e1;
}
.validating {
  background: rgba(56, 189, 248, 0.16);
  border-color: rgba(56, 189, 248, 0.45);
  color: #7dd3fc;
}
.pending_review {
  background: rgba(251, 191, 36, 0.16);
  border-color: rgba(251, 191, 36, 0.45);
  color: #fcd34d;
}
.approved {
  background: rgba(163, 230, 53, 0.16);
  border-color: rgba(163, 230, 53, 0.45);
  color: #bef264;
}
.published {
  background: rgba(74, 222, 128, 0.18);
  border-color: rgba(74, 222, 128, 0.5);
  color: #4ade80;
}
.deprecated {
  background: rgba(251, 146, 60, 0.16);
  border-color: rgba(251, 146, 60, 0.45);
  color: #fdba74;
}
.retired {
  background: rgba(100, 116, 139, 0.2);
  border-color: rgba(100, 116, 139, 0.5);
  color: #94a3b8;
}
.unknown {
  background: rgba(148, 163, 184, 0.12);
  border-color: rgba(148, 163, 184, 0.3);
  color: #94a3b8;
}
</style>
