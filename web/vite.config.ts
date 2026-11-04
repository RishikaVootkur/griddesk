import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const SAMPLE = "sample/eval-data.sample.json";

/** Static build only: ship the sample so a preview works before the real eval-data.json exists. */
function emitEvalSample(): Plugin {
  return {
    name: "griddesk-emit-eval-sample",
    apply: "build",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "eval-data.sample.json", source: readFileSync(SAMPLE, "utf8") });
    },
  };
}

// `--mode static` builds only the eval dashboard, for GitHub Pages.
// It reads ./eval-data.json from its own folder and needs no backend.
// The sample data ships only in that build (and is imported by `--mode mock`).
export default defineConfig(({ mode }) => {
  const isStatic = mode === "static";
  return {
    base: isStatic ? "./" : "/",
    plugins: [react(), tailwindcss(), ...(isStatic ? [emitEvalSample()] : [])],
    define: {
      __STATIC_MODE__: JSON.stringify(isStatic),
    },
    build: {
      outDir: isStatic ? "dist-static" : "dist",
      chunkSizeWarningLimit: 900,
    },
    server: {
      proxy: {
        "/api": { target: "http://localhost:8000", changeOrigin: true },
      },
    },
    test: {
      environment: "node",
      include: ["src/**/*.test.ts"],
    },
  };
});
