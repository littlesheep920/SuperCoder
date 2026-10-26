import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
    watch: {
      /**
       * Editors that save atomically create a temporary directory next to the
       * file. On Windows those files stay locked briefly, and chokidar throwing
       * EBUSY on them takes the whole dev server down — it happened repeatedly
       * while editing these views. None of these patterns are source worth
       * reloading, so ignoring them is correct rather than merely convenient.
       */
      ignored: [
        "**/.*.tmpdir/**",
        "**/*.tmpdir/**",
        "**/*.tmp",
        "**/screenshots/**",
        "**/.vite/**",
      ],
    },
  },
});
