import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `--mode static` builds only the eval dashboard, for GitHub Pages.
// It reads ./eval-data.json from its own folder and needs no backend.
export default defineConfig(({ mode }) => {
  const isStatic = mode === "static";
  return {
    base: isStatic ? "./" : "/",
    plugins: [react(), tailwindcss()],
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
