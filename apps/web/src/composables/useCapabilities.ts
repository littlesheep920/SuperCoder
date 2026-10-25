/**
 * Caller capabilities for UI gating.
 *
 * The server is the only authorization boundary. This module exists so the
 * console does not render actions the caller cannot perform — a dead button
 * reads as a bug and wastes time.
 *
 * Two rules keep it honest:
 *
 * 1. **Fail closed.** If the identity request fails, or the response is
 *    malformed, the capability set is empty and the UI is read-only. Guessing
 *    would risk showing actions that the server then refuses.
 * 2. **Never infer.** No role names are inspected here; the server decides what
 *    a role may do. Adding a role check in the front end would create a second
 *    source of truth that can silently disagree with the backend.
 */

import { computed, readonly, ref } from "vue";
import { api } from "../api/client";
import type { CallerIdentity, Capability } from "../api/types";

const identity = ref<CallerIdentity | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

/**
 * Capabilities the caller holds.
 *
 * Empty until the identity loads, and empty if loading fails. An empty set means
 * "offer no mutations", which is the safe direction to be wrong in.
 */
const capabilities = computed<ReadonlySet<Capability>>(
  () => new Set(identity.value?.capabilities ?? []),
);

/** True only after a successful load, so the UI can distinguish "unknown" from "none". */
const loaded = computed(() => identity.value !== null);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    identity.value = await api.getCallerIdentity();
  } catch (cause) {
    // Fail closed: dropping to read-only is always safe, whereas keeping stale
    // capabilities after a token change could offer actions that now fail.
    identity.value = null;
    error.value = cause instanceof Error ? cause.message : "无法获取调用者身份";
  } finally {
    loading.value = false;
  }
}

function reset(): void {
  identity.value = null;
  error.value = null;
}

/** Whether the caller may attempt the named operation. */
function can(capability: Capability): boolean {
  return capabilities.value.has(capability);
}

/** Whether the caller may attempt at least one of the named operations. */
function canAny(...wanted: Capability[]): boolean {
  return wanted.some((capability) => capabilities.value.has(capability));
}

export function useCapabilities() {
  return {
    identity: readonly(identity),
    capabilities,
    loaded,
    loading: readonly(loading),
    error: readonly(error),
    load,
    reset,
    can,
    canAny,
  };
}
